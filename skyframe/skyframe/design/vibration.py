"""PRELIMINARY AISC Design Guide 11 walking-vibration screening (v0.20).

Simplified DG11 Chapter 4 walking-excitation check for horizontal floor
beams that support a meshed shell slab (the same slab-detection geometry
as the composite module — the effective width is reused as the DG11
panel width):

* Natural frequency (DG11 Eq. 3-3, the classic Rayleigh drop-in)::

      fn = 0.18 * sqrt(g / delta)        [Hz], g = 9.81 m/s^2

  with ``delta`` the MIDSPAN deflection (chord-relative, station 6 of
  11) under the SUSTAINED vibration weight ``Dead + live_factor*Live``
  — DG11 recommends the actual sustained load, not the design live
  load; ``live_factor`` defaults to 0.11 (~0.5 kPa of an office's
  ~4.8 kPa nominal live load, the DG11 Chapter 4 office assumption).
  The deflection superposes the two EXACT v0.16 bare-steel per-case
  recoveries: ``delta = |delta_D + live_factor*delta_L|`` — using the
  bare-steel I is CONSERVATIVE (composite action stiffens the floor and
  raises fn; documented simplification).
* Peak acceleration ratio (DG11 Eq. 4-1)::

      ap/g = P0 * exp(-0.35 * fn) / (beta * W_eff)

  ``P0 = 0.29 kN`` (65 lb walking force), ``beta`` = damping ratio
  (default 0.03).  ``W_eff = w * beff_v * span`` with ``w`` the
  sustained distributed weight (kPa) and ``beff_v`` the composite
  effective width — implemented as ``W_eff = W_beam * beff_v / trib``
  where ``W_beam = |V2(0) - V2(L)|`` is the total transverse load the
  beam carries (the exact station-shear drop across the span) and
  ``trib`` the summed per-side available widths (the beam's tributary
  width), so ``W_beam/(trib*span)`` recovers w and the beff_v/trib
  ratio applies the DG11 panel width (documented simplification of the
  DG11 modal panel weight).
* Acceptance — ``ap/g <= ap_limit`` (default 0.005, DG11 Table 4-1
  offices); a note flags ``fn < 3 Hz`` (outside the walking-excitation
  fit).

THIS IS A PRELIMINARY SCREENING CHECK, NOT A FINAL DG11 EVALUATION: no
girder/joist combined-mode frequency, no continuity/mode-shape panel
factors, no rhythmic or sensitive-equipment chapters.  Every result
carries ``"preliminary": True``.

Units: SkyFrame SI — kN, m, kPa, Hz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from skyframe.design.concrete import _case_block
from skyframe.design.composite import (_beam_widths, _chord_relative_dy,
                                       _collect_slabs, _find_slab,
                                       default_live_case)
from skyframe.design.punching import default_gravity_case

__all__ = [
    "VibrationCheck", "check_vibration", "natural_frequency",
    "walking_acceleration", "P0_WALKING", "DEFAULT_BETA",
    "DEFAULT_LIVE_FACTOR", "DEFAULT_AP_LIMIT", "GRAVITY",
]

P0_WALKING = 0.29           # kN, DG11 walking force (65 lb)
DEFAULT_BETA = 0.03         # damping ratio (offices)
DEFAULT_LIVE_FACTOR = 0.11  # sustained live share (offices, documented)
DEFAULT_AP_LIMIT = 0.005    # ap/g acceptance, DG11 Table 4-1 offices
GRAVITY = 9.81              # m/s^2
_TOL = 1e-6


def natural_frequency(delta: float, g: float = GRAVITY) -> float:
    """DG11 Eq. 3-3: fn = 0.18*sqrt(g/delta) [Hz]; delta in metres."""
    if delta <= 0.0:
        raise ValueError("natural_frequency: delta must be > 0")
    return 0.18 * math.sqrt(g / delta)


def walking_acceleration(fn: float, W_eff: float, *,
                         beta: float = DEFAULT_BETA,
                         P0: float = P0_WALKING) -> float:
    """DG11 Eq. 4-1: ap/g = P0*exp(-0.35*fn)/(beta*W_eff); W_eff in kN."""
    if fn <= 0.0 or W_eff <= 0.0 or beta <= 0.0 or P0 <= 0.0:
        raise ValueError("walking_acceleration: fn, W_eff, beta, P0 must "
                         "be > 0")
    return P0 * math.exp(-0.35 * fn) / (beta * W_eff)


def _station_load(blk: dict, uid: str) -> Optional[float]:
    """Total transverse load on the member: V2(0) - V2(L) (exact statics —
    the internal shear drops by exactly the applied transverse load)."""
    st = (blk.get("member_stations") or {}).get(uid)
    if not st or not st.get("V2"):
        return None
    v = st["V2"]
    return float(v[0]) - float(v[-1])


@dataclass
class VibrationCheck:
    """Outcome of one walking-vibration screen (SI: kN, m, Hz)."""

    uid: str
    story: str = ""
    applicable: bool = False
    reason: str = ""
    fn: Optional[float] = None            # Hz
    delta_mid: Optional[float] = None     # m, sustained midspan deflection
    W_eff: Optional[float] = None         # kN
    ap_over_g: Optional[float] = None
    limit: float = DEFAULT_AP_LIMIT
    status: str = "N/A"                   # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True              # ALWAYS True — screening check

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "story": self.story,
            "applicable": self.applicable, "reason": self.reason,
            "fn": self.fn, "delta_mid": self.delta_mid,
            "W_eff": self.W_eff, "ap_over_g": self.ap_over_g,
            "limit": self.limit, "status": self.status,
            "notes": list(self.notes), "preliminary": True,
        }


def check_vibration(model, results, case: Optional[str] = None,
                    live_case: Optional[str] = None, *,
                    live_factor: float = DEFAULT_LIVE_FACTOR,
                    beta: float = DEFAULT_BETA,
                    ap_limit: float = DEFAULT_AP_LIMIT,
                    P0: float = P0_WALKING,
                    g: float = GRAVITY) -> List[VibrationCheck]:
    """DG11 walking screen for every horizontal slab-supporting beam.

    Parameters
    ----------
    model       : the BuildingModel the results were computed from.
    results     : engine results object or CONTRACT.md results dict
                  (member stations + deflections required).
    case        : DEAD (sustained) static case / additive combo; default
                  = the first DEAD-classified case.  Unknown raises.
    live_case   : live case entering at ``live_factor``; default = the
                  first LIVE-classified case (dead-only with a note when
                  the model has none).
    live_factor : sustained share of the live case (>= 0).
    beta        : damping ratio; ap_limit: acceptance on ap/g.

    Returns one :class:`VibrationCheck` per BEAM member (model order);
    beams without a supporting slab or without deflection stations are
    ``applicable: false`` with a reason.
    """
    for name, v in (("live_factor", live_factor),):
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v < 0.0):
            raise ValueError(f"{name!r} must be a finite value >= 0, "
                             f"got {v!r}")
    for name, v in (("beta", beta), ("ap_limit", ap_limit), ("P0", P0),
                    ("g", g)):
        if (isinstance(v, bool) or not isinstance(v, (int, float))
                or not math.isfinite(v) or v <= 0.0):
            raise ValueError(f"{name!r} must be a finite value > 0, "
                             f"got {v!r}")

    d_res = results.to_dict() if hasattr(results, "to_dict") else results
    if case is None:
        case = default_gravity_case(model)
    blk = _case_block(d_res, case)               # raises KeyError on unknown
    if live_case is None:
        live_case = default_live_case(model)
    live_blk = _case_block(d_res, live_case) if live_case else None

    slabs = _collect_slabs(model, None)
    checks: List[VibrationCheck] = []
    for m in model.members:
        if m.kind != "beam":
            continue
        chk = VibrationCheck(uid=m.uid, story=m.story, limit=float(ap_limit))
        checks.append(chk)
        if abs(float(m.pi[2]) - float(m.pj[2])) > _TOL:
            chk.reason = "not horizontal"
            continue
        slab = _find_slab(slabs, m)
        if slab is None:
            chk.reason = ("no horizontal shell slab contains the beam "
                          "midpoint at its elevation")
            continue
        span = m.length
        rel_d = _chord_relative_dy(blk, m.uid, span)
        W_d = _station_load(blk, m.uid)
        if rel_d is None or W_d is None:
            chk.reason = "no deflection/station results for this member"
            continue
        mid = len(rel_d) // 2                     # station 6 of 11: x = L/2
        delta = rel_d[mid]
        W = W_d
        if live_blk is not None:
            rel_l = _chord_relative_dy(live_blk, m.uid, span)
            W_l = _station_load(live_blk, m.uid)
            if rel_l is not None:
                delta += live_factor * rel_l[mid]
            if W_l is not None:
                W += live_factor * W_l
        else:
            chk.notes.append("no LIVE-classified case: sustained weight = "
                             "Dead only")
        delta = abs(delta)
        W = abs(W)
        if delta <= _TOL * 1e-3 or W <= _TOL:
            chk.reason = ("no gravity deflection/load under the vibration "
                          "combo")
            continue
        widths = _beam_widths(model, m, slab)
        beff = (min(span / 8.0, widths["left"])
                + min(span / 8.0, widths["right"]))
        trib = widths["trib"]
        chk.applicable = True
        chk.delta_mid = delta
        chk.W_eff = W * (beff / trib) if trib > _TOL else W
        chk.fn = natural_frequency(delta, g)
        chk.ap_over_g = walking_acceleration(chk.fn, chk.W_eff, beta=beta,
                                             P0=P0)
        if chk.fn < 3.0:
            chk.notes.append("fn < 3 Hz: outside the DG11 walking-"
                             "excitation fit")
        chk.status = "OK" if chk.ap_over_g <= ap_limit else "NG"
    return checks
