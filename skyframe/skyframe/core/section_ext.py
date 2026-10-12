"""FrameSection v1.21 extensions: parametric ``dims``, database reference
``db_ref``, unsymmetric-section ``principal`` data and the
``use_principal`` analysis switch (CONTRACT "Steel section database and
parametric shapes").

Serialisation: each field is emitted by ``FrameSection.to_dict`` ONLY when
set (``dims`` / ``db_ref`` / ``principal`` not None, ``use_principal``
True), so every model without them saves byte-identically.

Unsymmetric sections (angles, concrete L) - product of inertia I23
    The ETABS / SAP2000 default is used: the section is analysed about its
    GEOMETRIC (leg-parallel) local axes 2 / 3 with the stored I33 / I22 and
    the product of inertia I23 IGNORED.  For a single angle that makes the
    member stiffer/softer than reality depending on the load direction
    (the classic "geometric-axis" idealisation).
    ``use_principal = True`` switches the section to PRINCIPAL-axis
    analysis: the engine (``principal_model`` at engine entry) uses
    ``principal.I_major`` as I33 and ``principal.I_minor`` as I22 and turns
    every member using the section by ``-alpha`` about local 1 (the
    member's stamped ``section_axis_offset``, added to ``angle`` by
    :func:`skyframe.core.frame_axes.member_local_axes`), so local 3 lies on
    the major principal axis.  This reproduces the full unsymmetric-bending
    solution exactly (tests/test_steel_db.py); member forces are then
    reported about the principal axes.
"""

from __future__ import annotations

import copy
import math
from typing import Optional

EXT_KEYS = ("dims", "db_ref", "principal", "use_principal")


def to_dict(d: dict) -> dict:
    """FrameSection.to_dict filter: drop unset extension fields."""
    for k in ("dims", "db_ref", "principal"):
        if d.get(k) is None:
            d.pop(k, None)
    if not d.get("use_principal"):
        d.pop("use_principal", None)
    return d


def fill_from_dims(name: str, sd: dict) -> dict:
    """A section JSON object given only ``shape`` + ``dims`` (no ``A``)
    gets A / I33 / I22 / J / b / h computed exactly from the dimensions
    (explicit keys in ``sd`` win).  Anything else is returned unchanged."""
    if "A" in sd or not isinstance(sd.get("dims"), dict):
        return sd
    from skyframe.core.shape_props import SHAPE_TYPES, section_kwargs
    typ = sd.get("shape")
    if typ not in SHAPE_TYPES:
        return sd
    nums = {k: v for k, v in sd["dims"].items() if not isinstance(v, str)}
    kw = section_kwargs(typ, nums, _torsion(sd["dims"]))
    kw.pop("As2"), kw.pop("As3")
    return {**kw, **sd}


def load(sec, sd: dict) -> None:
    """Read the extension fields of one section JSON object (defensive)."""
    dims = sd.get("dims")
    if isinstance(dims, dict):
        sec.dims = {str(k): (v if isinstance(v, str) else float(v))
                    for k, v in dims.items()}
    ref = sd.get("db_ref")
    if isinstance(ref, dict):
        sec.db_ref = {"standard": str(ref.get("standard", "")),
                      "name": str(ref.get("name", ""))}
    pr = sd.get("principal")
    if isinstance(pr, dict):
        sec.principal = {k: float(pr[k]) for k in ("I_major", "I_minor",
                                                   "alpha") if k in pr}
    sec.use_principal = bool(sd.get("use_principal", False))
    exact_from_dims(sec)                    # v1.22 (no-op unless opted in)


def exact_from_dims(sec) -> None:
    """v1.22: a parametric section with ``dims["torsion"] == "exact"``
    carries the finite-element J / As2 / As3 (skyframe.core.section_fem,
    cached by geometry hash) whatever values the file holds, so the stored
    method always matches the stored numbers.  No-op otherwise (every
    other section loads byte-identically)."""
    dims = getattr(sec, "dims", None)
    if _torsion(dims) != "exact":
        return
    from skyframe.core.shape_props import SHAPE_TYPES, section_kwargs
    if getattr(sec, "shape", None) not in SHAPE_TYPES:
        return
    try:
        kw = section_kwargs(sec.shape, {k: v for k, v in dims.items()
                                        if not isinstance(v, str)}, "exact")
    except (ValueError, KeyError, ArithmeticError):
        return                              # validate() reports bad dims
    sec.J, sec.As2, sec.As3 = kw["J"], kw["As2"], kw["As3"]


def _torsion(dims) -> str:
    """v1.22: ``dims["torsion"] == "exact"`` opts a parametric section in
    to the finite-element J / Cw (skyframe.core.section_fem)."""
    return "exact" if isinstance(dims, dict) \
        and dims.get("torsion") == "exact" else "closed"


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def validate(sec) -> None:
    """ValueError on malformed extension fields (no-op when unset)."""
    nm = getattr(sec, "name", "?")
    dims = getattr(sec, "dims", None)
    if dims is not None:
        if not isinstance(dims, dict):
            raise ValueError(f"Section {nm}: dims must be an object")
        for k, v in dims.items():
            if isinstance(v, str):
                continue
            if not _finite(v) or v < 0:
                raise ValueError(f"Section {nm}: dims.{k} must be a finite "
                                 f"number >= 0 (got {v!r})")
        from skyframe.core.shape_props import SHAPE_TYPES, validate_dims
        if dims.get("torsion") not in (None, "closed", "exact"):
            raise ValueError(f"Section {nm}: dims.torsion must be 'closed' "
                             f"or 'exact' (got {dims.get('torsion')!r})")
        if getattr(sec, "shape", None) in SHAPE_TYPES:
            try:
                validate_dims(sec.shape, {k: v for k, v in dims.items()
                                          if not isinstance(v, str)})
            except ValueError as exc:
                raise ValueError(f"Section {nm}: {exc}") from None
    ref = getattr(sec, "db_ref", None)
    if ref is not None and not (isinstance(ref, dict)
                                and isinstance(ref.get("standard"), str)
                                and isinstance(ref.get("name"), str)
                                and ref["standard"] and ref["name"]):
        raise ValueError(f"Section {nm}: db_ref must be "
                         "{\"standard\": str, \"name\": str}")
    pr = getattr(sec, "principal", None)
    if pr is not None:
        if not (isinstance(pr, dict)
                and all(_finite(pr.get(k)) for k in ("I_major", "I_minor",
                                                     "alpha"))
                and pr["I_major"] > 0 and pr["I_minor"] > 0
                and pr["I_major"] >= pr["I_minor"]):
            raise ValueError(f"Section {nm}: principal must hold finite "
                             "I_major >= I_minor > 0 and alpha (rad)")
    if getattr(sec, "use_principal", False) and pr is None:
        raise ValueError(f"Section {nm}: use_principal needs the principal "
                         "properties {I_major, I_minor, alpha}")


# --------------------------------------------------------------------------- #
# principal-axis analysis
# --------------------------------------------------------------------------- #
def axis_offset_deg(sec) -> float:
    """Extra member rotation (deg about local 1) for a principal-axis
    section: -alpha, so local 3 aligns with the major principal axis."""
    if sec is None or not getattr(sec, "use_principal", False):
        return 0.0
    pr = getattr(sec, "principal", None) or {}
    return -math.degrees(float(pr.get("alpha", 0.0)))


def uses_principal(model) -> bool:
    return any(getattr(s, "use_principal", False)
               for s in model.sections.values())


def stamp(model) -> None:
    """Stamp every member's ``section_axis_offset`` from its section."""
    for m in model.members:
        off = axis_offset_deg(model.sections.get(m.section))
        if off or getattr(m, "section_axis_offset", 0.0):
            m.section_axis_offset = off


def principal_model(model):
    """The model the engine analyses w.r.t. principal axes: ``model``
    ITSELF when no section sets ``use_principal`` (bit-identical default);
    otherwise a deep copy whose principal sections carry I33 = I_major,
    I22 = I_minor, with every member stamped (the source members are
    stamped too, so local-axis reports agree)."""
    if not uses_principal(model):
        return model
    stamp(model)
    new = copy.deepcopy(model)
    for s in new.sections.values():
        if getattr(s, "use_principal", False) and s.principal:
            s.I33 = float(s.principal["I_major"])
            s.I22 = float(s.principal["I_minor"])
    stamp(new)
    return new


def principal_from_dims(sec) -> Optional[dict]:
    """``principal`` for a parametric unsymmetric section (None else)."""
    from skyframe.core.shape_props import principal_of
    if not isinstance(getattr(sec, "dims", None), dict):
        return None
    try:
        return principal_of(sec.shape, {k: v for k, v in sec.dims.items()
                                        if not isinstance(v, str)})
    except (ValueError, KeyError):
        return None


def section_from_dims(name: str, material: str, typ: str, dims: dict,
                      **kw):
    """A FrameSection for a parametric shape: exact A / I33 / I22 / J,
    drawing b / h, shear areas As2 / As3, ``shape = typ``, ``dims`` and,
    for unsymmetric types, ``principal``.  Extra keyword arguments are
    FrameSection fields (modifiers ...)."""
    from skyframe.core.model import FrameSection
    from skyframe.core.shape_props import principal_of, section_kwargs
    nums = {k: v for k, v in dims.items() if not isinstance(v, str)}
    p = section_kwargs(typ, nums, _torsion(dims))
    As2, As3 = p.pop("As2"), p.pop("As3")
    sec = FrameSection(name=name, material=material, **p, **kw)
    sec.As2, sec.As3 = As2, As3
    sec.shape = typ
    sec.dims = {k: (v if isinstance(v, str) else float(v))
                for k, v in dims.items()}
    sec.principal = principal_of(typ, nums)
    return sec
