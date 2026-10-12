"""Frequency-dependent link properties for Steady-State / PSD cases.

CONTRACT "Frequency-dependent link properties".

``BuildingModel.link_freq_props`` (ETABS Define > Section Properties >
Frequency Dependent Link/Support Properties)::

    {name: {"links": [link uid, ...],
            "dofs": {DOF: [[f_hz, K, C], ...], ...}}}

* ``DOF``: ``"UX" "UY" "UZ" "RX" "RY" "RZ"`` — the GLOBAL relative
  deformation ``u_j - u_i`` of an ``elastic`` link; ``"AXIAL"`` — the
  elongation along ``pi -> pj`` of a ``damper`` link.  Other link types
  are rejected (their linear frequency-domain stiffness is not a single
  scalar per DOF).
* ``K`` (kN/m or kN*m/rad) and ``C`` (kN*s/m or kN*m*s/rad) are the TOTAL
  stiffness and viscous coefficient at frequency ``f`` (Hz); they REPLACE
  the link's elastic value on that DOF (``stiffness[d]`` for elastic; a
  damper has no frequency-domain stiffness of its own) in Steady-State
  and PSD cases only — every other analysis keeps the elastic link.
* Linear interpolation in ``f``; constant (end value) beyond the table.
  Frequencies strictly increasing, >= 0; ``K`` finite, ``C >= 0``.
* A link belongs to at most one property.

Empty (default) -> nothing emitted by ``to_dict``; frequency cases of
such models are byte-identical.  Solver: :mod:`skyframe.engine.link_freq`.

This module does NOT import :mod:`skyframe.core.model` at import time.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

import numpy as np

GLOBAL_DOFS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
AXIAL = "AXIAL"
LFD_DOFS = GLOBAL_DOFS + (AXIAL,)
_KEYS = {"links", "dofs"}


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def _validate_table(where: str, tab) -> None:
    if (not isinstance(tab, (list, tuple)) or len(tab) < 1
            or not all(isinstance(r, (list, tuple)) and len(r) == 3
                       and all(_num(v) for v in r) for r in tab)):
        raise ValueError(f"{where}: table must be a non-empty list of "
                         "[f_hz, K, C] rows of finite numbers")
    fs = [float(r[0]) for r in tab]
    if fs[0] < 0.0 or any(b <= a for a, b in zip(fs[:-1], fs[1:])):
        raise ValueError(f"{where}: frequencies must be >= 0 and strictly "
                         "increasing")
    if any(float(r[2]) < 0.0 for r in tab):
        raise ValueError(f"{where}: C must be >= 0")


def validate(model) -> None:
    props = getattr(model, "link_freq_props", None) or {}
    if not isinstance(props, dict):
        raise ValueError("link_freq_props must be a {name: property} dict")
    links = {lk.uid: lk for lk in model.links}
    owner: Dict[str, str] = {}
    for name, p in props.items():
        where = f"Frequency-dependent link property {name!r}"
        if not isinstance(name, str) or not name.strip():
            raise ValueError("frequency-dependent link property names must "
                             "be non-empty strings")
        if not isinstance(p, dict) or set(p) - _KEYS:
            raise ValueError(f"{where}: must be a dict with keys {sorted(_KEYS)}")
        dofs = p.get("dofs")
        if not isinstance(dofs, dict) or not dofs:
            raise ValueError(f"{where}: 'dofs' must map at least one DOF to "
                             "a [[f_hz, K, C], ...] table")
        for lab, tab in dofs.items():
            if lab not in LFD_DOFS:
                raise ValueError(f"{where}: DOF must be one of {LFD_DOFS}, "
                                 f"got {lab!r}")
            _validate_table(f"{where} {lab}", tab)
        uids = p.get("links", [])
        if not isinstance(uids, list):
            raise ValueError(f"{where}: 'links' must be a list of link uids")
        for uid in uids:
            lk = links.get(uid)
            if lk is None:
                raise ValueError(f"{where}: unknown link {uid!r}")
            if uid in owner:
                raise ValueError(f"{where}: link {uid!r} already has "
                                 f"property {owner[uid]!r}")
            owner[uid] = name
            lt = getattr(lk, "link_type", "elastic")
            if lt == "elastic":
                if AXIAL in dofs:
                    raise ValueError(f"{where}: AXIAL applies to damper "
                                     f"links; {uid!r} is elastic (use "
                                     "UX..RZ)")
            elif lt == "damper":
                if set(dofs) != {AXIAL}:
                    raise ValueError(f"{where}: damper link {uid!r} takes "
                                     "the AXIAL DOF only")
            else:
                raise ValueError(f"{where}: link {uid!r} is {lt!r}; "
                                 "frequency-dependent properties apply to "
                                 "elastic and damper links")


def normalize(p: dict) -> dict:
    return {"links": [str(u) for u in p.get("links", [])],
            "dofs": {str(k): [[float(r[0]), float(r[1]), float(r[2])]
                              for r in tab]
                     for k, tab in p.get("dofs", {}).items()}}


def model_to_dict(model) -> dict:
    props = getattr(model, "link_freq_props", None) or {}
    return ({"link_freq_props": {str(k): normalize(v)
                                 for k, v in props.items()}}
            if props else {})


def model_from_dict(model, d: dict) -> None:
    props = d.get("link_freq_props")
    if props is None:
        return
    if not isinstance(props, dict):
        raise ValueError("link_freq_props must be a {name: property} dict")
    out = {str(k): normalize(v) for k, v in props.items()}
    uids = {lk.uid for lk in model.links}
    for p in out.values():                # prune links deleted since saved
        p["links"] = [u for u in p["links"] if u in uids]
    model.link_freq_props = out


def interp(tab: Sequence[Sequence[float]], f: np.ndarray
           ) -> Tuple[np.ndarray, np.ndarray]:
    """(K(f), C(f)) — linear in f, end values held beyond the table."""
    fs = np.array([r[0] for r in tab], dtype=float)
    ks = np.array([r[1] for r in tab], dtype=float)
    cs = np.array([r[2] for r in tab], dtype=float)
    f = np.asarray(f, dtype=float)
    return np.interp(f, fs, ks), np.interp(f, fs, cs)


def assignments(model) -> List[Tuple[str, str, str, list]]:
    """``[(prop name, link uid, dof, table), ...]`` in a stable order."""
    out = []
    for name, p in (getattr(model, "link_freq_props", None) or {}).items():
        for uid in p.get("links", []):
            for lab in LFD_DOFS:
                if lab in p["dofs"]:
                    out.append((name, uid, lab, p["dofs"][lab]))
    return out


__all__ = ["GLOBAL_DOFS", "AXIAL", "LFD_DOFS", "validate", "normalize",
           "model_to_dict", "model_from_dict", "interp", "assignments"]
