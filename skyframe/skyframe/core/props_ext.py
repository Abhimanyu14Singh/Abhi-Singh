"""Wave B property extensions - serialisation / validation hub.

One import in :mod:`skyframe.core.model`; every hook there calls a function
of this module.  Features (CONTRACT "Orthotropic materials, slab / deck
property types, cold-formed and composite sections, named line / area
springs"):

* ``Material.ortho`` (:mod:`skyframe.core.ortho`)
* ``ShellSection.slab`` / ``laminate`` / ``distribution`` /
  ``one_way_dir`` (:mod:`skyframe.core.slab_props`, :mod:`.ortho`)
* ``ShellRegion.distribution_override``
* ``LineSpring.property``, ``ShellRegion.area_spring = {"property"}``,
  ``model.line_spring_properties`` / ``area_spring_properties``
  (:mod:`skyframe.core.spring_dist`)
* composite SRC / CFT frame sections (:mod:`skyframe.core.composite_col`)

Every field is emitted only when set, so a model without them saves
byte-identically.
"""

from __future__ import annotations

from typing import Dict

from . import aniso as _aniso            # Wave C anisotropic / angle
from . import composite_col as _cc
from . import ortho as _ortho
from . import slab_props as _slab
from . import spring_dist as _sd

SHELL_SECTION_KEYS = ("slab", "laminate", "distribution", "one_way_dir")


# --------------------------------------------------------------------------- #
# to_dict filters
# --------------------------------------------------------------------------- #
def material_to_dict(d: dict) -> dict:
    if d.get("ortho") is None:
        d.pop("ortho", None)
    return _aniso.material_to_dict(d)               # Wave C


def shell_section_to_dict(d: dict) -> dict:
    for k in SHELL_SECTION_KEYS:
        if d.get(k) is None:
            d.pop(k, None)
    return _aniso.shell_section_to_dict(d)          # Wave C


def region_to_dict(r) -> dict:
    return ({"distribution_override": True}
            if getattr(r, "distribution_override", False) else {})


def line_spring_to_dict(ls) -> dict:
    p = getattr(ls, "property", None)
    return {"property": p} if p is not None else {}


def model_to_dict(model) -> dict:
    out = {}
    lp = getattr(model, "line_spring_properties", None)
    ap = getattr(model, "area_spring_properties", None)
    if lp:
        out["line_spring_properties"] = {k: _sd.normalize(v)
                                         for k, v in lp.items()}
    if ap:
        out["area_spring_properties"] = {k: _sd.normalize(v)
                                         for k, v in ap.items()}
    return out


# --------------------------------------------------------------------------- #
# from_dict
# --------------------------------------------------------------------------- #
def area_spring_from_dict(asp):
    """The ``area_spring`` dict of a region (named or inline)."""
    if asp is None:
        return None
    if asp.get("property") is not None:
        return {"property": str(asp["property"])}
    return {"kz": float(asp["kz"]),
            "compression_only": bool(asp.get("compression_only", False))}


def model_from_dict(mdl, d: dict) -> None:
    _aniso.model_from_dict(mdl, d)                  # Wave C material angle
    for name, sd in (d.get("shell_sections") or {}).items():
        ss = mdl.shell_sections.get(name)
        if ss is None:
            continue
        if sd.get("slab") is not None:
            ss.slab = _slab.normalize_slab(sd["slab"])
        if sd.get("laminate") is not None:
            ss.laminate = {"layers": [
                {"t": float(la["t"]), "material": str(la["material"]),
                 "angle": float(la.get("angle", 0.0))}
                for la in sd["laminate"].get("layers") or []]}
        if sd.get("distribution") is not None:
            ss.distribution = str(sd["distribution"])
        if sd.get("one_way_dir") is not None:
            ss.one_way_dir = int(sd["one_way_dir"])
        _slab.sync_thickness(ss, mdl.materials.get(ss.material))
    for r, rd in zip(mdl.shells, d.get("shells") or []):
        if rd.get("distribution_override"):
            r.distribution_override = True
    for ls, sd in zip(mdl.line_springs, d.get("line_springs") or []):
        if sd.get("property") is not None:
            ls.property = str(sd["property"])
    for key in ("line_spring_properties", "area_spring_properties"):
        src = d.get(key)
        if src:
            if not isinstance(src, dict):
                raise ValueError(f"{key} must be an object")
            setattr(mdl, key, {str(k): _sd.normalize(v)
                               for k, v in src.items()})


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate_model(model) -> None:
    _aniso.validate_model(model)                    # Wave C
    for mat in model.materials.values():
        _ortho.validate_material(mat)
    for ss in model.shell_sections.values():
        mat = model.materials.get(ss.material)
        _ortho.validate_laminate(ss, model.materials)
        _slab.validate_slab(ss, mat)
        _slab.validate_distribution(ss)
        if mat is not None and _ortho.is_ortho(mat) and \
                getattr(ss, "layered", None) and \
                not _aniso.layered_general(ss):             # Wave C
            raise ValueError(f"Shell section {ss.name}: a layered "
                             "(nonlinear) section cannot use the "
                             "orthotropic material directly")
    for r in model.shells:
        if not isinstance(getattr(r, "distribution_override", False), bool):
            raise ValueError(f"Shell {r.uid}: distribution_override must "
                             "be a bool")
    for sec in model.sections.values():
        _cc.validate_section(sec, model.materials)
    _sd.validate_model(model)
    _slab.validate_drops(model)


def summary(model) -> Dict[str, dict]:
    """{section name: slab / laminate summary} (API / tables helper)."""
    out = {}
    for n, ss in model.shell_sections.items():
        mat = model.materials.get(ss.material)
        if _slab.slab_type(ss) is not None:
            out[n] = _slab.summary(ss, mat)
    return out
