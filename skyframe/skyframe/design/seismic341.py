"""PRELIMINARY AISC 341-16 special-moment-frame joint checks (v0.23).

For SMF-designated columns (a caller-supplied uid list, or ``"auto"`` =
every column) this module screens each interior beam-column joint — the
SAME joint set as the v0.17 panel-zone machinery
(:func:`skyframe.engine.opensees_engine._panel_zone_joints`: any deduped
point where at least one flexural COLUMN end meets at least one BEAM
end) — against two AISC 341-16 E3 provisions:

* Strong-column / weak-beam (E3.4a, Eq. E3-1)::

      sum(Mpc*) / sum(Mpb*) >= 1.0
      Mpc* = Zc * (Fyc - Puc/Ag)        per column end at the joint
      Mpb* = 1.1 * Ry * Fyb * Zb        per beam end at the joint

  with ``Puc`` the column's compressive demand from the chosen combo
  (LRFD; taken as ``max(N_end_i, 0)`` — the engine's +compression
  convention), ``Ry = 1.1`` (A992, Table A3.1) and Z the MAJOR-axis
  plastic modulus from the AISC design table (bending is assumed about
  each member's major axis — documented; skewed/weak-axis framing is not
  resolved).  The ``Muv`` shear-eccentricity amplification of Mpb* and
  the E3.4a(b) exemptions (low-axial single-story / top-story joints)
  are NOT applied — documented conservatism/simplification.
* Panel-zone shear (E3.6e -> AISC 360-16 J10.6)::

      Ru      = sum(Mpb*) / (db - tf)          (demand: the beam flange
                force couples of the fully yielded, strain-hardened
                beams; db = deepest connecting beam, tf its flange)
      phiRn   = phi * 0.60 * Fy * dc * tw *
                (1 + 3*bcf*tcf^2 / (db*dc*tw)),   phi = 1.0

  Eq. J10-11 (panel-zone deformation considered in the analysis,
  Pr <= 0.75 Pc assumed — documented), NO doubler plates, column
  properties from the DEEPEST VERTICAL column at the joint (same
  governing-column rule as the scissors machinery).  ``phi = 1.0`` per
  AISC 360-16 J10.6.

Every joint result carries ``point``, per-side sums, ``scwb_ratio``,
``pz_demand``/``pz_capacity``/``pz_ratio`` and ``status`` ("OK" when
SCWB >= 1 AND the panel zone passes; "N/A" with a note when any
connecting member is not a recognised library W-shape or lacks
demands).  THESE ARE PRELIMINARY SCREENING CHECKS — no lateral-bracing,
protected-zone, width-thickness (D1.1), column-splice, or connection
prequalification checks.

Units: SkyFrame SI — kN, m, kPa (Fy = 345 MPa passed as 345_000 kPa).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Union

from skyframe.design.steel import _case_block, design_properties

__all__ = ["JointCheck341", "check_seismic341", "summarize_341",
           "RY_A992", "PHI_PZ", "panel_zone_capacity"]

RY_A992 = 1.1     # AISC 341 Table A3.1 (A992 W-shapes)
PHI_PZ = 1.0      # AISC 360-16 J10.6 (panel-zone shear yielding)
_TOL = 1e-6


def panel_zone_capacity(Fy: float, dc: float, tw: float, bcf: float,
                        tcf: float, db: float, *,
                        phi: float = PHI_PZ) -> float:
    """phi*Rn per AISC 360-16 Eq. J10-11 [kN] (no doubler plate).

    ``phi * 0.60 * Fy * dc * tw * (1 + 3*bcf*tcf^2/(db*dc*tw))`` — the
    "panel-zone deformation considered, Pr <= 0.75 Pc" branch
    (documented assumption; the axial-interaction reduction of J10-12
    is not applied).
    """
    if min(Fy, dc, tw, bcf, tcf, db) <= 0.0:
        raise ValueError("panel_zone_capacity: all inputs must be > 0")
    return phi * 0.60 * Fy * dc * tw * (
        1.0 + 3.0 * bcf * tcf ** 2 / (db * dc * tw))


@dataclass
class JointCheck341:
    """Outcome of one SMF joint check (SI: kN, m, kN*m)."""

    point: tuple                       # (x, y, z)
    columns: List[str] = field(default_factory=list)   # column uids
    beams: List[str] = field(default_factory=list)     # beam uids
    sum_Mpc: float = 0.0               # kN*m
    sum_Mpb: float = 0.0               # kN*m
    scwb_ratio: Optional[float] = None  # sum(Mpc*)/sum(Mpb*)
    pz_demand: Optional[float] = None   # Ru (kN)
    pz_capacity: Optional[float] = None  # phi*Rn (kN)
    pz_ratio: Optional[float] = None    # Ru / phiRn
    status: str = "N/A"                # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True

    def to_dict(self) -> dict:
        return {"point": list(self.point), "columns": list(self.columns),
                "beams": list(self.beams), "sum_Mpc": self.sum_Mpc,
                "sum_Mpb": self.sum_Mpb, "scwb_ratio": self.scwb_ratio,
                "pz_demand": self.pz_demand,
                "pz_capacity": self.pz_capacity,
                "pz_ratio": self.pz_ratio, "status": self.status,
                "notes": list(self.notes), "preliminary": True}


def _w_props(model, member) -> Optional[dict]:
    """Library W-shape design properties for a member (or None)."""
    sec = model.sections.get(member.section)
    if sec is None:
        return None
    p = design_properties(sec.name)
    if p is None or abs(sec.A - p["A"]) > 0.01 * p["A"]:
        return None
    return p


def check_seismic341(model, results, case_or_combo: str,
                     columns: Union[str, List[str]] = "auto", *,
                     Fy: float = 345_000.0, Ry: float = RY_A992,
                     phi_pz: float = PHI_PZ) -> List[JointCheck341]:
    """AISC 341 SCWB + panel-zone screens at SMF beam-column joints.

    Parameters
    ----------
    model         : BuildingModel the results were computed from.
    results       : engine results object (with .to_dict()) or the
                    CONTRACT.md results dict.
    case_or_combo : name of the case/combo supplying the column axial
                    demands Puc.
    columns       : ``"auto"`` (every column participates) or a list of
                    COLUMN uids designating the SMF columns — only
                    joints touching at least one designated column are
                    checked.  Unknown uids raise ValueError.
    Fy, Ry        : nominal yield [kPa] and the 341 Table A3.1 overstrength
                    ratio (Fy applies to BOTH beams and columns —
                    per-member grades are not resolved, documented).
    phi_pz        : panel-zone resistance factor (default 1.0, J10.6).

    Returns one :class:`JointCheck341` per qualifying joint, sorted by
    (z, x, y) of the joint point.  Empty list when no joint qualifies.
    """
    from skyframe.engine.opensees_engine import _panel_zone_joints

    if not (math.isfinite(Fy) and Fy > 0.0):
        raise ValueError("Fy must be a finite value > 0 (kPa)")
    if not (math.isfinite(Ry) and Ry > 0.0):
        raise ValueError("Ry must be a finite value > 0")
    col_uids = {m.uid for m in model.members if m.kind == "column"}
    if isinstance(columns, str):
        if columns != "auto":
            raise ValueError("'columns' must be \"auto\" or a list of "
                             f"column uids, got {columns!r}")
        designated = col_uids
    else:
        designated = set()
        for uid in columns:
            if uid not in col_uids:
                raise ValueError(f"'columns' entry {uid!r} is not a column "
                                 "uid")
            designated.add(uid)

    case = _case_block(results, case_or_combo)
    forces = case.get("member_forces") or {}

    checks: List[JointCheck341] = []
    joints = _panel_zone_joints(model)
    for key in sorted(joints, key=lambda k: (k[2], k[0], k[1])):
        kinds = joints[key]
        if not any(m.uid in designated for m, _ in kinds["column"]):
            continue
        chk = JointCheck341(point=tuple(key))
        checks.append(chk)
        chk.columns = [m.uid for m, _ in kinds["column"]]
        chk.beams = [m.uid for m, _ in kinds["beam"]]

        # ---- gather W-shape properties + demands ----------------------- #
        bad = False
        col_data, beam_data = [], []
        for m, _end in kinds["column"]:
            p = _w_props(model, m)
            if p is None:
                chk.notes.append(f"column {m.uid!r}: not a library W-shape "
                                 "— joint not checked")
                bad = True
                continue
            mf = forces.get(m.uid)
            if mf is None:
                chk.notes.append(f"column {m.uid!r}: no demand in "
                                 f"{case_or_combo!r} — joint not checked")
                bad = True
                continue
            Puc = max(float(mf[0]), 0.0)          # +compression only
            col_data.append((m, p, Puc))
        for m, _end in kinds["beam"]:
            p = _w_props(model, m)
            if p is None:
                chk.notes.append(f"beam {m.uid!r}: not a library W-shape "
                                 "— joint not checked")
                bad = True
                continue
            beam_data.append((m, p))
        if bad or not col_data or not beam_data:
            continue

        # ---- E3-1 strong column / weak beam ---------------------------- #
        chk.sum_Mpc = sum(p["Zx"] * (Fy - Puc / p["A"])
                          for _m, p, Puc in col_data)
        chk.sum_Mpb = sum(1.1 * Ry * Fy * p["Zx"] for _m, p in beam_data)
        chk.scwb_ratio = (chk.sum_Mpc / chk.sum_Mpb
                          if chk.sum_Mpb > 0.0 else math.inf)
        chk.notes.append("SCWB E3-1: Mpc* = Zc*(Fy - Puc/Ag), Mpb* = "
                         f"1.1*Ry*Fy*Zb (Ry = {Ry:g}); Muv and the E3.4a "
                         "exemptions not applied")

        # ---- panel zone: J10-11 vs the beam flange-couple demand ------- #
        vcols = [(m, p, Puc) for m, p, Puc in col_data
                 if math.hypot(m.pj[0] - m.pi[0],
                               m.pj[1] - m.pi[1]) < _TOL]
        if not vcols:
            chk.notes.append("no vertical column at the joint — panel zone "
                             "not checked")
        else:
            _mc, pc, _Puc = max(vcols, key=lambda t: t[1]["d"])
            deepest = max(beam_data, key=lambda t: t[1]["d"])[1]
            db, tfb = deepest["d"], deepest["tf"]
            chk.pz_demand = chk.sum_Mpb / (db - tfb)
            chk.pz_capacity = panel_zone_capacity(
                Fy, pc["d"], pc["tw"], pc["bf"], pc["tf"], db, phi=phi_pz)
            chk.pz_ratio = chk.pz_demand / chk.pz_capacity
            chk.notes.append("panel zone J10-11 (Pr <= 0.75Pc assumed, no "
                             "doubler); demand = sum(Mpb*)/(db - tf)")

        ok = chk.scwb_ratio >= 1.0 and (chk.pz_ratio is None
                                        or chk.pz_ratio <= 1.0)
        chk.status = "OK" if ok else "NG"
    return checks


def summarize_341(checks: List[JointCheck341]) -> dict:
    """Roll-up: counts, min SCWB ratio, max panel-zone ratio."""
    ok = sum(1 for c in checks if c.status == "OK")
    ng = sum(1 for c in checks if c.status == "NG")
    na = sum(1 for c in checks if c.status == "N/A")
    scwb = [c.scwb_ratio for c in checks if c.scwb_ratio is not None]
    pz = [c.pz_ratio for c in checks if c.pz_ratio is not None]
    return {"n": len(checks), "ok": ok, "ng": ng, "na": na,
            "min_scwb": min(scwb) if scwb else None,
            "max_pz_ratio": max(pz) if pz else None,
            "preliminary": True}
