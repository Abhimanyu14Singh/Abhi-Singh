"""Generalized displacements (ETABS Define > Generalized Displacements).

A generalized displacement is a named LINEAR COMBINATION of joint degrees
of freedom::

    g = sum_k  sf_k * u_k(point_k, dof_k)

``BuildingModel.generalized_displacements`` (emitted by ``to_dict`` only
when non-empty, so legacy models are byte-identical)::

    {"<name>": {"type": "translation" | "rotation",
                "terms": [{"point": [x, y, z], "dof": "UX", "sf": 1.0},
                          ...]}}

* ``dof`` is one of UX UY UZ RX RY RZ (global axes);
* ``sf`` is a finite scale factor (a translation term may scale a
  rotation by a length, e.g. ``h * RZ``, exactly as ETABS allows);
* ``type`` only decides the reported unit (length vs angle).

Classic use: an inter-story drift ``(u_top - u_bottom) / h`` is
``{"type": "translation", "terms": [{top, UX, 1/h}, {bottom, UX, -1/h}]}``.

Results are pure post-processing of the solved joint displacements
(:func:`compute`), tabulated by :mod:`skyframe.core.tables_wave_c`
("Generalized Displacements"); no engine change.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Tuple

GD_TYPES = ("translation", "rotation")
GD_DOFS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
_TERM_KEYS = ("point", "dof", "sf")
SNAP_TOL = 1e-6


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)))


def normalize_term(t: dict) -> dict:
    """Canonical term dict (point floats, dof upper, sf float)."""
    return {"point": [float(v) for v in t["point"]],
            "dof": str(t["dof"]).upper(),
            "sf": float(t.get("sf", 1.0))}


def normalize(gd: dict) -> dict:
    return {"type": str(gd.get("type", "translation")),
            "terms": [normalize_term(t) for t in gd.get("terms") or []]}


def validate(model) -> None:
    """Raise ``ValueError`` on a malformed definition (called from
    ``BuildingModel.validate`` through a one-line hook)."""
    gds = getattr(model, "generalized_displacements", None) or {}
    if not isinstance(gds, dict):
        raise ValueError("generalized_displacements must be an object "
                         "{name: definition}")
    for name, gd in gds.items():
        where = f"Generalized displacement {name!r}"
        if not isinstance(name, str) or not name.strip():
            raise ValueError("generalized displacement names must be "
                             "non-empty strings")
        if not isinstance(gd, dict):
            raise ValueError(f"{where}: must be an object")
        bad = sorted(set(gd) - {"type", "terms"})
        if bad:
            raise ValueError(f"{where}: unknown keys {bad} "
                             "(allowed: type, terms)")
        if gd.get("type", "translation") not in GD_TYPES:
            raise ValueError(f"{where}: type must be one of {GD_TYPES}, "
                             f"got {gd.get('type')!r}")
        terms = gd.get("terms")
        if not isinstance(terms, list) or not terms:
            raise ValueError(f"{where}: terms must be a non-empty list")
        for k, t in enumerate(terms):
            tw = f"{where}: terms[{k}]"
            if not isinstance(t, dict):
                raise ValueError(f"{tw} must be an object")
            bad = sorted(set(t) - set(_TERM_KEYS))
            if bad:
                raise ValueError(f"{tw}: unknown keys {bad}")
            p = t.get("point")
            if (not isinstance(p, (list, tuple)) or len(p) != 3
                    or not all(_finite(v) for v in p)):
                raise ValueError(f"{tw}.point must be [x, y, z] finite "
                                 "numbers")
            if str(t.get("dof", "")).upper() not in GD_DOFS:
                raise ValueError(f"{tw}.dof must be one of {GD_DOFS}, got "
                                 f"{t.get('dof')!r}")
            if not _finite(t.get("sf", 1.0)):
                raise ValueError(f"{tw}.sf must be a finite number")


def model_to_dict(model) -> dict:
    gds = getattr(model, "generalized_displacements", None) or {}
    if not gds:
        return {}
    return {"generalized_displacements": {k: copy.deepcopy(v)
                                          for k, v in gds.items()}}


def model_from_dict(model, d: dict) -> None:
    raw = d.get("generalized_displacements")
    if not raw:
        return
    if not isinstance(raw, dict):
        raise ValueError("generalized_displacements must be an object")
    model.generalized_displacements = {str(k): copy.deepcopy(v)
                                       for k, v in raw.items()}


# --------------------------------------------------------------------------- #
# evaluation
# --------------------------------------------------------------------------- #
def resolve_tags(nodes: Dict[int, Tuple[float, float, float]],
                 gd: dict, prefer=(), exclude=()) -> List[Optional[int]]:
    """FE node tag of every term's point (lowest structural tag at the
    point; ``None`` when no node lies within ``SNAP_TOL``)."""
    out = []
    for t in gd.get("terms") or []:
        p = [float(v) for v in t["point"]]
        hits = [tag for tag, c in nodes.items() if tag not in exclude
                and all(abs(c[k] - p[k]) < SNAP_TOL for k in range(3))]
        pref = [tag for tag in hits if tag in prefer]
        out.append(min(pref or hits) if hits else None)
    return out


def evaluate(gd: dict, tags: List[Optional[int]], node_disp: dict
             ) -> Optional[float]:
    """``sum sf * u`` for one displacement field ``{tag: [6]}`` (``None``
    when a term's joint is missing)."""
    total = 0.0
    for t, tag in zip(gd.get("terms") or [], tags):
        if tag is None:
            return None
        u = node_disp.get(str(tag), node_disp.get(tag))
        if u is None:
            return None
        total += float(t.get("sf", 1.0)) * float(
            u[GD_DOFS.index(str(t["dof"]).upper())])
    return total


def evaluate_series(gd: dict, series: List[Dict[str, List[float]]]
                    ) -> Optional[List[float]]:
    """Time series of ``g`` from per-term recorded joint series
    (``series[k]`` = the plot-function ``disp`` dict of term k's joint)."""
    out = None
    for t, s in zip(gd.get("terms") or [], series):
        if s is None:
            return None
        v = s.get(str(t["dof"]).upper())
        if v is None:
            return None
        sf = float(t.get("sf", 1.0))
        if out is None:
            out = [sf * float(x) for x in v]
        else:
            out = [a + sf * float(x) for a, x in zip(out, v)]
    return out
