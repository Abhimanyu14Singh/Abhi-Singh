"""Wave C model fields -- one aggregator so ``core/model.py`` carries only
one-line hooks (CONTRACT "Frame pier labels", "Per-object auto edge
constraints", "Static seismic eccentricity overrides", "Diaphragm design
forces (ASCE 7 12.10)").

New fields (all default to the legacy behaviour and are serialised ONLY
when set, so a legacy model's JSON is byte-identical):

* ``FrameMember.pier``            -- pier label on a frame (core.frame_piers)
* ``ShellRegion.edge_constraint`` -- ``None`` (follow the model-wide
  ``edge_constraints``), ``True`` / ``False`` per area
  (core.edge_constraints_ext)
* ``LoadPattern.ecc_overrides``   -- per story / story-diaphragm
  eccentricity overrides of a static seismic pattern (core.static_ecc)
* ``BuildingModel.pier_labels``   -- defined pier labels
* ``BuildingModel.fpx_settings``  -- ASCE 7 12.10 Fpx inputs (core.fpx)
"""

from __future__ import annotations

import copy

from . import frame_piers as _fpr


def member_to_dict(m) -> dict:
    return _fpr.tag_to_dict(m)


def shell_to_dict(r) -> dict:
    ec = getattr(r, "edge_constraint", None)
    return {} if ec is None else {"edge_constraint": ec}


def pattern_to_dict(p) -> dict:
    ov = getattr(p, "ecc_overrides", None)
    return {} if not ov else {"ecc_overrides": copy.deepcopy(ov)}


def model_to_dict(model) -> dict:
    out = dict(_fpr.model_to_dict(model))
    fs = getattr(model, "fpx_settings", None)
    if fs:
        out["fpx_settings"] = copy.deepcopy(fs)
    return out


def model_from_dict(model, d: dict) -> None:
    _fpr.model_from_dict(model, d)
    for r, rd in zip(model.shells, d.get("shells") or []):
        if isinstance(rd, dict) and rd.get("edge_constraint") is not None:
            r.edge_constraint = rd["edge_constraint"]
    for name, pd in (d.get("patterns") or {}).items():
        pat = model.patterns.get(pd.get("name", name)) if isinstance(
            pd, dict) else None
        if pat is not None and pd.get("ecc_overrides"):
            pat.ecc_overrides = copy.deepcopy(pd["ecc_overrides"])
    fs = d.get("fpx_settings")
    if fs:
        model.fpx_settings = copy.deepcopy(fs)


def validate(model) -> None:
    from . import edge_constraints_ext as _ece
    from . import fpx as _fpx
    from . import static_ecc as _secc
    _fpr.validate(model)
    _ece.validate(model)
    for pat in model.patterns.values():
        _secc.validate(model, pat)
    _fpx.validate(model)
