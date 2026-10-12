"""Material-proportional (composite) modal damping.

CONTRACT "Material-proportional (composite) modal damping".

``BuildingModel.material_damping`` is a plain ``{material name: zeta}``
dict (ETABS: Material Property Data > Advanced > modal damping ratio).
A time-history case with ``damping_model == "composite"`` gets one
viscous damping ratio PER MODE, the strain-energy weighted average of
the ratios of the materials that store the mode's strain energy:

    zeta_i = sum_g zeta_g E_g,i / sum_g E_g,i,
    E_g,i  = 1/2 phi_i^T K_g phi_i      (elements of group g)

Groups: every material named by a frame section (frame elements) or a
slab/wall section (shell elements); every other element (links, springs,
hinge / panel-zone springs, members whose section names no material) is
the ``"other"`` group.  Materials absent from ``material_damping`` and
the ``"other"`` group take the case's flat ``damping``, so an empty dict
reproduces constant modal damping ``zeta_i = damping``.

The per-mode ratios then drive the existing ``"modal"`` damping path
(direct integration ``ops.modalDamping``; FNA modal equations) — the
engine runs the case as ``damping_model = "modal"`` with ``modal_zeta``
= the composite ratios (``skyframe.engine.composite_damping``).

Defaults: ``material_damping`` empty -> nothing is emitted by
``to_dict`` (legacy models serialise byte-identically); legacy
``damping_model`` values are untouched.

This module does NOT import :mod:`skyframe.core.model` at import time.
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping

COMPOSITE = "composite"
OTHER = "other"


def _num(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def validate(model) -> None:
    md = getattr(model, "material_damping", None) or {}
    if not isinstance(md, dict):
        raise ValueError("material_damping must be a {material: zeta} dict")
    for name, z in md.items():
        if name not in model.materials:
            raise ValueError(f"material_damping: unknown material {name!r}")
        if not (_num(z) and 0.0 <= z < 1.0):
            raise ValueError(f"material_damping[{name!r}] must be a damping "
                             "ratio in [0, 1)")


def model_to_dict(model) -> dict:
    md = getattr(model, "material_damping", None) or {}
    return ({"material_damping": {str(k): float(v) for k, v in md.items()}}
            if md else {})


def model_from_dict(model, d: dict) -> None:
    md = d.get("material_damping")
    if md is None:
        return
    if not isinstance(md, dict):
        raise ValueError("material_damping must be a {material: zeta} dict")
    model.material_damping = {str(k): float(v) for k, v in md.items()
                              if str(k) in model.materials}   # prune stale


def composite_zeta(energies: Mapping[str, float],
                   ratios: Mapping[str, float], default: float) -> float:
    """Strain-energy weighted ratio of one mode (groups not in ``ratios``
    take ``default``); 0-energy modes return ``default``."""
    tot = sum(max(e, 0.0) for e in energies.values())
    if tot <= 0.0:
        return float(default)
    return float(sum(max(e, 0.0) * float(ratios.get(g, default))
                     for g, e in energies.items()) / tot)


def mode_ratios(energies: List[Mapping[str, float]],
                ratios: Mapping[str, float], default: float) -> List[float]:
    return [composite_zeta(e, ratios, default) for e in energies]


__all__ = ["COMPOSITE", "OTHER", "validate", "model_to_dict",
           "model_from_dict", "composite_zeta", "mode_ratios"]
