"""Updated-capacity P-M2-M3 hinges (Wave C) — CONTRACT "Wave C hinges".

Method (documented exactly).  An interacting hinge with
``axial.source == "analysis"`` and/or ``drop_strength == "drops"`` is the
same ``Elliptical`` coupled-plasticity section as the legacy hinge, but
its strength is driven explicitly, step by step:

* the section is built with a negligible kinematic hardening
  (``UH_PARALLEL_RATIO * k_member``) and its two yield moments are OpenSees
  PARAMETERS (``Fy1`` = M2y, ``Fy2`` = M3y; ``updateParameter`` verified
  to move the yield surface of a live ``Elliptical`` section);
* after every COMMITTED step (the per-step hinge recorder of pushover,
  nonlinear static and nonlinear time history) the hinge reads its own
  axial force ``P`` (the local-x force of the tie element, tension +;
  sign flipped at an end-j hinge) and the running maximum resultant
  plastic deformation ``dp`` (backbone units, as the state classifier);
* the next step's yield moments are ``r(dp) * M_y(P)``: ``M_y(P)`` from
  the interaction surface at ``P`` (the 0/90 curves, ellipse fit) and
  ``r`` the backbone force ratio (:func:`core.interaction_hinges.
  capacity_ratio` — B -> C hardening, the C -> D drop, D -> E, residual).

The capacity therefore LAGS the axial force by one step (explicit
update; the error is O(dP per step)); a section whose current moment
exceeds a lowered capacity returns to the new yield surface on the next
step (the drop).  A hinge without either option keeps the legacy build
(bit-identical).
"""

from __future__ import annotations

import math

import openseespy.opensees as ops

from skyframe.core import interaction_hinges as _cih

PARAM_TAG0 = 700000          # OpenSees parameter tags (own namespace)


def active(prop: dict) -> bool:
    ax = prop.get("axial") or {}
    return (ax.get("source") == "analysis"
            or prop.get("drop_strength", "holds") == "drops")


def _next_tag(asm) -> int:
    t = getattr(asm, "_wc_param", PARAM_TAG0) + 1
    asm._wc_param = t
    return t


def register(asm, sec_ele: int, tie_ele: int, end_j: bool, M2y: float,
             M3y: float, P: float) -> dict:
    """Create the two yield-moment parameters; returns the update state."""
    p1, p2 = _next_tag(asm), _next_tag(asm)
    ops.parameter(p1, "element", sec_ele, "Fy1")
    ops.parameter(p2, "element", sec_ele, "Fy2")
    return {"p1": p1, "p2": p2, "tie": tie_ele, "sign": -1.0 if end_j
            else 1.0, "dp_max": 0.0, "M2y0": M2y, "M3y0": M3y,
            "P": P, "M2y": M2y, "M3y": M3y}


def axial_force(st: dict) -> float:
    f = ops.eleResponse(st["tie"], "basicForce")
    return st["sign"] * float(f[0]) if len(f) else 0.0


def update(spec: dict, dpn: float, hh: dict) -> None:
    """After a committed step: new yield moments for the next one."""
    st = spec["env"]["update"]
    prop = spec["prop"]
    st["dp_max"] = max(st["dp_max"], dpn)
    if prop["axial"] is not None and prop["axial"]["source"] == "analysis":
        P = axial_force(st)
        ell = _cih.ellipse_at(prop["surface"], P)
        M2, M3 = ell["M2y"], ell["M3y"]
    else:
        P, M2, M3 = st["P"], st["M2y0"], st["M3y0"]
    r = _cih.capacity_ratio(prop, st["dp_max"])
    floor = 1.0e-6
    st["P"], st["M2y"], st["M3y"] = P, max(r * M2, floor * st["M2y0"]), \
        max(r * M3, floor * st["M3y0"])
    ops.updateParameter(st["p1"], st["M2y"])
    ops.updateParameter(st["p2"], st["M3y"])
    hh.setdefault("P_step", []).append(P)
    hh.setdefault("capacity_M2", []).append(st["M2y"])
    hh.setdefault("capacity_M3", []).append(st["M3y"])
    hh.setdefault("capacity_ratio", []).append(r)
    if not math.isfinite(st["M2y"] + st["M3y"]):
        raise RuntimeError("PMM capacity update produced a non-finite "
                           "yield moment")
