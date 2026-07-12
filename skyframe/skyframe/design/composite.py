"""PRELIMINARY AISC 360-16 Chapter I3 composite beam design checks (v0.20).

Screens every HORIZONTAL steel W-shape floor beam that supports a meshed
SHELL slab against the AISC 360-16 composite-beam provisions (simply
supported interior gravity beams with a concrete slab on steel deck,
headed studs — the classic ETABS "Composite Beam Design" scope):

* Applicability — a member is checked when it (1) is ``kind == "beam"``
  and HORIZONTAL (|dz| <= 1e-6), (2) carries a recognised library W-shape
  (same name + 1%-area validation as :mod:`skyframe.design.steel`), (3)
  supports a slab — a horizontal ``ShellRegion(kind="slab", behavior ==
  "shell")`` in the beam's plane (z within 1e-6) whose plan polygon
  contains the beam MIDPOINT (the punching-module slab-detection
  geometry), and (4) has analysis demands.  Anything else is reported
  ``applicable: false`` with a ``reason``.
* Effective width (I3.1a) — per side of the beam
  ``beff_side = min(span/8, s_neighbor/2, edge_distance)`` where
  ``s_neighbor`` is the PERPENDICULAR plan distance to the nearest
  PARALLEL beam at the same elevation whose midpoint lies inside the same
  slab, and ``edge_distance`` is the distance from the beam midpoint to
  the slab polygon's BOUNDING BOX along the perpendicular (documented
  simplification: a non-rectangular slab uses its bbox, so re-entrant
  edges are ignored); ``beff = beff_left + beff_right``.
* Full-composite strength (I3.2a, plastic stress distribution) — with
  ``tc = t_slab - hr`` the SOLID slab above the deck ribs (rib concrete
  ignored — deck ribs perpendicular to the beam, the usual case)::

      Cf = min(As*Fy, 0.85*fc'*beff*tc);   a = C / (0.85*fc'*beff)

  and ALL THREE plastic-neutral-axis cases.  Distances y are measured UP
  from the TOP OF STEEL; the slab soffit sits at ``hr``, top of slab at
  ``t_slab``; the steel section is doubly symmetric (centroid at d/2).
  The plastic moment is assembled as the datum-independent force sum
  ``Mn = sum(C_i*y_i) - sum(T_i*y_i)`` written as a SUPERPOSITION: the
  whole shape fully yielded in tension (force Fy*As at y = -d/2)
  plus a 2*Fy compression correction on the steel area ``A_c`` above the
  PNA::

      A_c = (As*Fy - C) / (2*Fy)                (equilibrium)
      A_c = 0            -> PNA in slab   (y_c n/a)
      A_c <= bf*tf       -> PNA in flange (x = A_c/bf,      y_c = -x/2)
      A_c >  bf*tf       -> PNA in web    (x = tf + (A_c - bf*tf)/tw,
                            y_c = -(bf*tf*tf/2 + A_w*(tf + A_w/(2*tw)))/A_c)
      Mn  = C*(t_slab - a/2) + Fy*As*d/2 + 2*Fy*A_c*y_c

  (PNA-in-slab reduces to the textbook ``As*Fy*(d/2 + t_slab - a/2)``;
  the web is taken at constant ``tw`` below the flange — the k-region
  fillet area, included in the table As, deepens x slightly, a documented
  approximation that never changes the forces).  ``phi_b = 0.90``.
* Partial composite (I3.2d + I8.2a) — stud strength::

      Qn = min(0.5*Asa*sqrt(fc'*Ec), Rg*Rp*Asa*Fu),  Rg = 1.0, Rp = 0.75

  (Rg = 1 / Rp = 0.75: one stud per rib welded through deck, the standard
  deck-perpendicular values; ``Ec = 4700*sqrt(fc'[MPa])`` MPa per ACI
  19.2.2.1, conversions explicit; in kPa/m^2 the concrete branch is
  directly kN).  One stud per rib at ``rib_spacing`` gives ``n_studs =
  floor((span/2)/rib_spacing)`` per shear span (max moment at midspan to
  the support), ``sumQn = n_studs*Qn``; then ``C = min(Cf, sumQn)`` in
  the same PNA math and ``ratio_composite = sumQn/Cf`` (< 0.25 noted per
  the AISC I3.2d.1 user note).
* Demand — ``Mu`` = max |M3| over the member stations, ENVELOPED over the
  requested combos (default: every ADDITIVE combo, exactly like
  :func:`skyframe.design.steel.check_members_envelope`); ``ratio =
  Mu / phiMn_partial``.
* Pre-composite (unshored, default) — the bare beam must carry the wet
  concrete: ``Mu_pre = 1.4 * M_dead`` (construction combo 1.4D on the
  first DEAD-classified case) against the Chapter F2 capacity with
  ``Lb = 0`` (the deck braces the top flange continuously during casting
  — documented assumption), i.e. ``phi_b*Mp = 0.9*Fy*Zx``.  ``shored =
  True`` skips the check (``precomp_ratio: null``).
* Deflection — lower-bound-style equivalent inertia (AISC Commentary
  Eq. C-I3-4)::

      I_equiv = Is + sqrt(sumQn/Cf) * (I_tr - Is)

  with ``I_tr`` the ELASTIC transformed inertia (modular ratio
  ``n = Es/Ec``; only the solid slab tc over beff, parallel-axis about
  the elastic NA — see :func:`transformed_inertia`; this is the standard
  substitute for the tabulated lower-bound I_LB, documented).  The
  live-load deflection is the v0.16 EXACT bare-steel deflection recovery
  (chord-relative max |dy| of the LIVE-classified case) SCALED by
  ``Is/I_equiv`` — exact for a prismatic beam because the elastic line is
  proportional to 1/I (documented approximation: the composite I applies
  to the full span, end connectivity unchanged).  Checked against
  ``span / model.deflection_limit`` (default L/360).

* Camber (v0.23) — a per-beam shop-camber RECOMMENDATION
  (:func:`camber_recommendation`, the industry 0.8x-dead-load rule of
  thumb): ``0.8 x`` the pre-composite dead-load deflection (the DEAD
  case's chord-relative bare-``Is`` elastic line), rounded DOWN to 5 mm
  increments, zero below 20 mm or for spans under 7.5 m.  Reported as
  ``camber`` (with ``defl_DL``) on every applicable beam; ``null`` when
  no dead-case deflection is recoverable.

THESE ARE PRELIMINARY SCREENING CHECKS, NOT A FINAL CODE CHECK: no
ductility check on the stud distribution, no rib-parallel deck case, no
long-term creep multiplier, no vibration (see
:mod:`skyframe.design.vibration`), no shear check.  Every result carries
``"preliminary": True``.

Units: SkyFrame SI — kN, m, kPa (Fy = 345 MPa passed as 345_000 kPa);
every MPa <-> kPa conversion is written explicitly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.design.concrete import _case_block
from skyframe.design.punching import _point_in_polygon, default_gravity_case
from skyframe.design.steel import (E_STEEL, _major_axis_capacity,
                                   design_properties)
from skyframe.design.wall import fc_from_E

__all__ = [
    "CompositeBeamCheck", "check_composite_beams", "composite_flexure",
    "stud_strength", "transformed_inertia", "equivalent_inertia",
    "default_live_case", "camber_recommendation", "PHI_B_COMPOSITE",
    "RG_STUD", "RP_STUD", "DEFAULT_HR", "DEFAULT_STUD_D",
    "DEFAULT_STUD_FU", "DEFAULT_RIB_SPACING", "CAMBER_FRACTION",
    "CAMBER_INCREMENT", "CAMBER_MIN", "CAMBER_MIN_SPAN",
]

PHI_B_COMPOSITE = 0.90      # AISC I3.2a flexure resistance factor
RG_STUD = 1.0               # I8.2a group factor: one stud per rib
RP_STUD = 0.75              # I8.2a position factor: welded through deck
DEFAULT_HR = 0.075          # m, deck rib height (3 in nominal)
DEFAULT_STUD_D = 0.019      # m, 19 mm (3/4 in) headed stud
DEFAULT_STUD_FU = 450_000.0  # kPa, stud tensile strength (65 ksi class)
DEFAULT_RIB_SPACING = 0.3   # m, deck rib pitch (12 in nominal)
_TOL = 1e-6

# v0.23 camber recommendation (industry rule of thumb, e.g. AISC Design
# Guide 36 / SDI practice — documented, not a code requirement):
CAMBER_FRACTION = 0.8       # camber 80% of the pre-composite DL deflection
CAMBER_INCREMENT = 0.005    # m — cambers are specified in 5 mm steps
CAMBER_MIN = 0.020          # m — below ~20 mm (3/4 in) don't camber
CAMBER_MIN_SPAN = 7.5       # m — short beams (< ~25 ft) are not cambered


def camber_recommendation(delta_dead: float, span: float) -> float:
    """Recommended shop camber [m] for one unshored composite beam.

    Industry rule of thumb (documented — fabrication practice, not an
    AISC requirement): camber ``CAMBER_FRACTION (0.8) x`` the
    PRE-COMPOSITE dead-load deflection (wet concrete on the bare steel
    ``Is``), rounded DOWN to ``CAMBER_INCREMENT`` (5 mm) steps; ZERO
    when the rounded value falls below ``CAMBER_MIN`` (20 mm) or the
    span is below ``CAMBER_MIN_SPAN`` (7.5 m) — small cambers are
    unreliable to fabricate and short beams are simply sized stiffer.
    """
    if not (math.isfinite(delta_dead) and delta_dead >= 0.0):
        raise ValueError("delta_dead must be a finite value >= 0 (m)")
    if not (math.isfinite(span) and span > 0.0):
        raise ValueError("span must be a finite value > 0 (m)")
    if span < CAMBER_MIN_SPAN:
        return 0.0
    c = math.floor(CAMBER_FRACTION * delta_dead / CAMBER_INCREMENT + 1e-12) \
        * CAMBER_INCREMENT
    return 0.0 if c < CAMBER_MIN else c


# --------------------------------------------------------------------------- #
# capacity building blocks (module-level so tests can exercise them directly)
# --------------------------------------------------------------------------- #
def stud_strength(d_stud: float, fc: float, Fu: float, *,
                  Rg: float = RG_STUD, Rp: float = RP_STUD) -> dict:
    """One headed stud's nominal strength Qn [kN] per AISC I8.2a.

    ``Qn = min(0.5*Asa*sqrt(fc'*Ec), Rg*Rp*Asa*Fu)`` with everything in
    kPa/m^2 (kPa * m^2 = kN, so the concrete branch needs no unit fixup);
    ``Ec = 4700*sqrt(fc'[MPa])`` MPa (ACI 19.2.2.1), conversions explicit.
    """
    if d_stud <= 0.0 or fc <= 0.0 or Fu <= 0.0:
        raise ValueError("stud_strength: d_stud, fc, Fu must be > 0")
    Asa = math.pi * d_stud ** 2 / 4.0                     # m^2
    Ec = 4700.0 * math.sqrt(fc / 1000.0) * 1000.0         # kPa (MPa*1000)
    q_conc = 0.5 * Asa * math.sqrt(fc * Ec)               # kN
    q_steel = Rg * Rp * Asa * Fu                          # kN
    return {"Qn": min(q_conc, q_steel), "Asa": Asa, "Ec": Ec,
            "Qn_concrete": q_conc, "Qn_steel": q_steel,
            "governs": "concrete" if q_conc <= q_steel else "steel"}


def composite_flexure(As: float, Fy: float, d: float, bf: float, tf: float,
                      tw: float, t_slab: float, hr: float, fc: float,
                      beff: float, sumQn: Optional[float] = None) -> dict:
    """Plastic-distribution composite Mn [kN*m] — AISC I3.2a, all PNA cases.

    ``sumQn = None`` gives the FULL-composite strength (C = Cf); a value
    caps the compression at the stud transfer (partial composite).  See
    the module docstring for the exact force-sum derivation.
    """
    if min(As, Fy, d, bf, tf, tw, t_slab, fc, beff) <= 0.0 or hr < 0.0:
        raise ValueError("composite_flexure: geometry/material must be > 0 "
                         "(hr >= 0)")
    tc = t_slab - hr
    if tc <= 0.0:
        raise ValueError(f"composite_flexure: tc = t_slab - hr = {tc:.4f} "
                         "m must be > 0")
    Cc_max = 0.85 * fc * beff * tc                        # kN
    Cf = min(As * Fy, Cc_max)                             # full-composite C
    C = Cf if sumQn is None else min(Cf, float(sumQn))
    if C <= 0.0:
        raise ValueError("composite_flexure: C must be > 0 (sumQn <= 0?)")
    a = C / (0.85 * fc * beff)                            # <= tc always
    y_conc = t_slab - a / 2.0                             # above top of steel
    A_c = (As * Fy - C) / (2.0 * Fy)                      # steel in compression
    if A_c <= _TOL * As:                                  # PNA in the slab
        A_c, y_c, x, pna = 0.0, 0.0, 0.0, "slab"
    elif A_c <= bf * tf + _TOL * As:                      # PNA in the flange
        x = A_c / bf
        y_c = -x / 2.0
        pna = "flange"
    else:                                                 # PNA in the web
        A_w = A_c - bf * tf
        x = tf + A_w / tw
        y_c = -(bf * tf * (tf / 2.0)
                + A_w * (tf + A_w / (2.0 * tw))) / A_c
        pna = "web"
    Mn = C * y_conc + Fy * As * d / 2.0 + 2.0 * Fy * A_c * y_c
    return {"Mn": Mn, "C": C, "Cf": Cf, "Cc_max": Cc_max, "a": a, "tc": tc,
            "pna": pna, "x_pna": x, "A_c": A_c, "y_conc": y_conc}


def transformed_inertia(As: float, Is: float, d: float, beff: float,
                        t_slab: float, hr: float, n: float) -> float:
    """Fully composite elastic transformed inertia I_tr [m^4].

    Only the SOLID slab ``tc = t_slab - hr`` over ``beff`` is transformed
    (area ``beff*tc/n``, centroid ``d/2 + hr + tc/2`` above the steel
    centroid); uncracked, short-term ``n = Es/Ec``::

        ybar = Ac_tr*y_slab / (As + Ac_tr)          (NA above steel centroid)
        I_tr = Is + As*ybar^2 + beff*tc^3/(12n) + Ac_tr*(y_slab - ybar)^2
    """
    tc = t_slab - hr
    if tc <= 0.0 or n <= 0.0 or As <= 0.0:
        raise ValueError("transformed_inertia: tc, n, As must be > 0")
    Ac = beff * tc / n
    y_slab = d / 2.0 + hr + tc / 2.0
    ybar = Ac * y_slab / (As + Ac)
    return (Is + As * ybar ** 2 + beff * tc ** 3 / (12.0 * n)
            + Ac * (y_slab - ybar) ** 2)


def equivalent_inertia(Is: float, I_tr: float, sumQn: float,
                       Cf: float) -> float:
    """Partial-composite effective inertia (AISC Commentary Eq. C-I3-4).

    ``I_equiv = Is + sqrt(sumQn/Cf) * (I_tr - Is)`` with the composite
    ratio clamped to [0, 1] (extra studs beyond full composite add no
    stiffness).  Endpoints: sumQn = Cf -> I_tr; sumQn -> 0 -> Is.
    """
    if Cf <= 0.0:
        raise ValueError("equivalent_inertia: Cf must be > 0")
    frac = min(max(sumQn / Cf, 0.0), 1.0)
    return Is + math.sqrt(frac) * (I_tr - Is)


def default_live_case(model) -> Optional[str]:
    """First load case that applies a LIVE-classified pattern (or None).

    Mirrors :func:`skyframe.design.punching.default_gravity_case` but
    returns ``None`` instead of raising — the live case only feeds the
    serviceability deflection, which is skipped with a note when absent.
    """
    for name, case in model.cases.items():
        for pname in case.patterns:
            pat = model.patterns.get(pname)
            if pat is not None and pat.kind == "live":
                return name
    return None


# --------------------------------------------------------------------------- #
# geometry helpers (shared with skyframe.design.vibration)
# --------------------------------------------------------------------------- #
def _collect_slabs(model, fc_prime: Optional[float]) -> List[dict]:
    """Horizontal meshed shell slabs with plan polygon, thickness and fc'.

    The same collection the punching module builds: fc' is the explicit
    ``fc_prime`` (kPa) or derived from the slab concrete's E via ACI
    19.2.2.1 inverted (:func:`skyframe.design.wall.fc_from_E`).
    """
    slabs: List[dict] = []
    for r in model.shells:
        if r.kind != "slab" or r.behavior != "shell":
            continue
        zs = [float(p[2]) for p in r.corners]
        if max(zs) - min(zs) > _TOL:
            continue                     # non-horizontal slab
        ssec = model.shell_sections.get(r.section)
        if ssec is None:
            continue
        mat = model.materials.get(ssec.material)
        fc = (float(fc_prime) if fc_prime is not None
              else fc_from_E(float(mat.E)) if mat is not None else 0.0)
        poly = [(float(p[0]), float(p[1])) for p in r.corners]
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        slabs.append({"region": r, "z": zs[0], "poly": poly,
                      "bbox": (min(xs), min(ys), max(xs), max(ys)),
                      "t": float(ssec.thickness), "fc": fc})
    return slabs


def _find_slab(slabs: List[dict], m) -> Optional[dict]:
    """The slab whose plane holds the beam and whose polygon contains the
    beam midpoint (boundary inclusive), or None."""
    z = float(m.pi[2])
    mx = (float(m.pi[0]) + float(m.pj[0])) / 2.0
    my = (float(m.pi[1]) + float(m.pj[1])) / 2.0
    for sl in slabs:
        if abs(sl["z"] - z) <= _TOL and _point_in_polygon(mx, my,
                                                          sl["poly"]):
            return sl
    return None


def _ray_to_bbox(mx: float, my: float, ux: float, uy: float,
                 bbox: Tuple[float, float, float, float]) -> float:
    """Distance from (mx, my) along unit (ux, uy) to the bbox boundary."""
    x0, y0, x1, y1 = bbox
    t = math.inf
    if abs(ux) > _TOL:
        t = min(t, ((x1 if ux > 0 else x0) - mx) / ux)
    if abs(uy) > _TOL:
        t = min(t, ((y1 if uy > 0 else y0) - my) / uy)
    return max(t, 0.0) if math.isfinite(t) else 0.0


def _beam_widths(model, m, slab: dict) -> dict:
    """Per-side available half-widths of one slab beam (m).

    For each side (along the plan perpendicular ``p = (-uy, ux)``):
    ``avail = min(s_neighbor/2, edge_distance)`` — s_neighbor from the
    nearest PARALLEL beam (plan direction cross product < 1e-6) at the
    same elevation whose midpoint lies in the same slab polygon;
    edge_distance from the slab polygon BOUNDING BOX (documented
    simplification).  ``trib`` = the summed available widths (the
    tributary width, used by the vibration W_eff).
    """
    dx = float(m.pj[0]) - float(m.pi[0])
    dy = float(m.pj[1]) - float(m.pi[1])
    Lp = math.hypot(dx, dy)
    ux, uy = dx / Lp, dy / Lp
    px, py = -uy, ux                              # plan perpendicular
    mx = (float(m.pi[0]) + float(m.pj[0])) / 2.0
    my = (float(m.pi[1]) + float(m.pj[1])) / 2.0
    z = float(m.pi[2])

    near: Dict[int, float] = {1: math.inf, -1: math.inf}
    for other in model.members:
        if other.uid == m.uid or other.kind != "beam":
            continue
        oz = (float(other.pi[2]), float(other.pj[2]))
        if abs(oz[0] - oz[1]) > _TOL or abs(oz[0] - z) > _TOL:
            continue                              # not horizontal at this z
        odx = float(other.pj[0]) - float(other.pi[0])
        ody = float(other.pj[1]) - float(other.pi[1])
        oL = math.hypot(odx, ody)
        if oL <= _TOL or abs(ux * ody - uy * odx) / oL > _TOL:
            continue                              # not parallel
        omx = (float(other.pi[0]) + float(other.pj[0])) / 2.0
        omy = (float(other.pi[1]) + float(other.pj[1])) / 2.0
        if not _point_in_polygon(omx, omy, slab["poly"]):
            continue                              # not on this slab
        off = (omx - mx) * px + (omy - my) * py
        if abs(off) <= _TOL:
            continue                              # collinear duplicate
        side = 1 if off > 0.0 else -1
        near[side] = min(near[side], abs(off))

    out = {}
    for side, sign in (("left", 1), ("right", -1)):
        edge = _ray_to_bbox(mx, my, sign * px, sign * py, slab["bbox"])
        half = near[sign] / 2.0
        out[side] = min(half, edge)
    out["trib"] = out["left"] + out["right"]
    return out


def _station_mu(blk: dict, uid: str) -> Optional[float]:
    """max |M3| over the member stations (fallback: the two end moments)."""
    st = (blk.get("member_stations") or {}).get(uid)
    if st and st.get("M3"):
        return max(abs(float(v)) for v in st["M3"])
    mf = (blk.get("member_forces") or {}).get(uid)
    if mf is None:
        return None
    return max(abs(float(mf[5])), abs(float(mf[11])))


def _chord_relative_dy(blk: dict, uid: str, L: float
                       ) -> Optional[List[float]]:
    """Chord-relative local-y deflections at the stations (v0.16 rule)."""
    md = (blk.get("member_deflections") or {}).get(uid)
    if not md or not md.get("dy"):
        return None
    dy, xs = md["dy"], md["x"]
    d0, d1 = float(dy[0]), float(dy[-1])
    return [float(v) - (d0 + (d1 - d0) * (float(x) / L))
            for v, x in zip(dy, xs)]


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #
@dataclass
class CompositeBeamCheck:
    """Outcome of one composite beam check (SI: kN, m, kPa)."""

    uid: str
    story: str = ""
    section: str = ""
    applicable: bool = False
    reason: str = ""                     # why not applicable ("" when it is)
    span: float = 0.0                    # m
    beff: float = 0.0                    # m (both sides)
    tc: float = 0.0                      # m, solid slab above the deck
    fc: float = 0.0                      # kPa
    C_full: float = 0.0                  # kN, full-composite C (= Cf)
    pna: str = ""                        # "slab" | "flange" | "web"
    phiMn_full: float = 0.0              # kN*m
    n_studs: int = 0                     # per HALF span (shear span)
    Qn: float = 0.0                      # kN per stud
    sumQn: float = 0.0                   # kN over the shear span
    ratio_composite: float = 0.0         # sumQn / Cf
    phiMn_partial: float = 0.0           # kN*m (governs the D/C)
    Mu: float = 0.0                      # kN*m, combo envelope
    ratio: Optional[float] = None        # Mu / phiMn_partial
    precomp_Mu: Optional[float] = None   # kN*m, 1.4*M_dead (unshored)
    precomp_ratio: Optional[float] = None
    I_s: float = 0.0                     # m^4, bare steel
    I_tr: float = 0.0                    # m^4, full transformed
    I_equiv: float = 0.0                 # m^4, C-I3-4 interpolation
    defl_LL: Optional[float] = None      # m, scaled live-load deflection
    defl_limit: Optional[float] = None   # m, span / deflection_limit
    defl_limit_ok: Optional[bool] = None
    camber: Optional[float] = None       # m, v0.23 recommendation (None =
    #   no dead-case deflection recoverable; 0.0 = below the thresholds)
    defl_DL: Optional[float] = None      # m, pre-composite dead deflection
    governing_combo: str = ""
    status: str = "N/A"                  # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True             # ALWAYS True — screening check only

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "story": self.story, "section": self.section,
            "applicable": self.applicable, "reason": self.reason,
            "span": self.span, "beff": self.beff, "tc": self.tc,
            "fc": self.fc, "C_full": self.C_full, "PNA_case": self.pna,
            "phiMn_full": self.phiMn_full, "n_studs": self.n_studs,
            "Qn": self.Qn, "sumQn": self.sumQn,
            "ratio_composite": self.ratio_composite,
            "phiMn_partial": self.phiMn_partial, "Mu": self.Mu,
            "ratio": self.ratio, "precomp_Mu": self.precomp_Mu,
            "precomp_ratio": self.precomp_ratio, "I_s": self.I_s,
            "I_tr": self.I_tr, "I_equiv": self.I_equiv,
            "defl_LL": self.defl_LL, "defl_limit": self.defl_limit,
            "defl_limit_ok": self.defl_limit_ok,
            "camber": self.camber, "defl_DL": self.defl_DL,
            "governing_combo": self.governing_combo, "status": self.status,
            "notes": list(self.notes), "preliminary": True,
        }


def _positive(name: str, v) -> float:
    if (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v) or v <= 0.0):
        raise ValueError(f"{name!r} must be a finite value > 0, got {v!r}")
    return float(v)


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_composite_beams(model, results,
                          combos: Optional[List[str]] = None, *,
                          fc_prime: Optional[float] = None,
                          t_slab: Optional[float] = None,
                          hr: float = DEFAULT_HR,
                          stud_d: float = DEFAULT_STUD_D,
                          stud_Fu: float = DEFAULT_STUD_FU,
                          rib_spacing: float = DEFAULT_RIB_SPACING,
                          shored: bool = False,
                          Fy: float = 345_000.0,
                          phi_b: float = PHI_B_COMPOSITE,
                          dead_case: Optional[str] = None,
                          live_case: Optional[str] = None
                          ) -> List[CompositeBeamCheck]:
    """AISC I3 composite checks for every slab-supporting W-shape beam.

    Parameters
    ----------
    model       : the BuildingModel the results were computed from.
    results     : engine results object (with ``.to_dict()``) or the
                  CONTRACT.md results dict (stations + deflections).
    combos      : combo/case names to envelope Mu over; default = every
                  ADDITIVE combo (ValueError when the model has none).
    fc_prime    : slab fc' (kPa); default derived from the slab concrete's
                  E via ACI 19.2.2.1 (``fc_from_E``).
    t_slab      : override slab thickness (m); default = each slab shell
                  section's thickness.
    hr          : deck rib height (m, >= 0; 0 = solid slab).
    stud_d      : headed-stud diameter (m); stud_Fu: tensile strength
                  (kPa); rib_spacing: one stud per rib at this pitch (m).
    shored      : True skips the pre-composite (wet concrete) check.
    dead_case   : construction-stage case; default = the first
                  DEAD-classified case.  live_case: serviceability case;
                  default = the first LIVE-classified case (skipped with
                  a note when the model has neither).

    Returns one :class:`CompositeBeamCheck` per BEAM member (model
    order) — non-applicable beams carry ``applicable: false`` + reason.
    """
    if not (isinstance(hr, (int, float)) and not isinstance(hr, bool)
            and math.isfinite(hr) and hr >= 0.0):
        raise ValueError(f"'hr' must be a finite value >= 0 (m), got {hr!r}")
    hr = float(hr)
    stud_d = _positive("stud_d", stud_d)
    stud_Fu = _positive("stud_Fu", stud_Fu)
    rib_spacing = _positive("rib_spacing", rib_spacing)
    Fy = _positive("Fy", Fy)
    if fc_prime is not None:
        fc_prime = _positive("fc_prime", fc_prime)
    if t_slab is not None:
        t_slab = _positive("t_slab", t_slab)

    beams = [m for m in model.members if m.kind == "beam"]
    if not beams:
        return []

    d_res = results.to_dict() if hasattr(results, "to_dict") else results
    if combos is None:
        combos = [name for name, cb in model.combos.items()
                  if getattr(cb, "combo_type", "add") == "add"]
        if not combos:
            raise ValueError("no additive combos to check — supply "
                             "'combos' (combo or static case names) or add "
                             "combinations to the model")
    blocks = {c: _case_block(d_res, c) for c in combos}   # KeyError -> caller

    if dead_case is None:
        try:
            dead_case = default_gravity_case(model)
        except ValueError:
            dead_case = None
    if live_case is None:
        live_case = default_live_case(model)
    dead_blk = _case_block(d_res, dead_case) if dead_case else None
    live_blk = _case_block(d_res, live_case) if live_case else None

    slabs = _collect_slabs(model, fc_prime)
    limit_den = float(getattr(model, "deflection_limit", 360.0))

    checks: List[CompositeBeamCheck] = []
    for m in beams:
        chk = CompositeBeamCheck(uid=m.uid, story=m.story,
                                 section=m.section)
        checks.append(chk)
        if abs(float(m.pi[2]) - float(m.pj[2])) > _TOL:
            chk.reason = "not horizontal"
            continue
        sec = model.sections.get(m.section)
        p = design_properties(sec.name) if sec is not None else None
        if p is None or abs(sec.A - p["A"]) > 0.01 * p["A"]:
            chk.reason = "not a library W-shape"
            continue
        slab = _find_slab(slabs, m)
        if slab is None:
            chk.reason = ("no horizontal shell slab contains the beam "
                          "midpoint at its elevation")
            continue
        t_sl = t_slab if t_slab is not None else slab["t"]
        fc = fc_prime if fc_prime is not None else slab["fc"]
        if fc <= 0.0:
            chk.reason = "slab fc' could not be resolved (pass fc_prime)"
            continue
        if t_sl - hr <= 0.0:
            chk.reason = (f"tc = t_slab - hr = {t_sl - hr:.4f} m <= 0 "
                          "(slab thinner than the deck ribs)")
            continue
        # demand envelope over the combos
        mus = {c: _station_mu(blk, m.uid) for c, blk in blocks.items()}
        mus = {c: v for c, v in mus.items() if v is not None}
        if not mus:
            chk.reason = "no analysis demand for this member"
            continue
        chk.governing_combo = max(mus, key=lambda c: mus[c])
        chk.Mu = mus[chk.governing_combo]
        chk.applicable = True
        chk.fc = fc
        span = m.length
        chk.span = span

        # ---- effective width (I3.1a) ---------------------------------- #
        widths = _beam_widths(model, m, slab)
        beff = (min(span / 8.0, widths["left"])
                + min(span / 8.0, widths["right"]))
        if beff <= _TOL:
            chk.applicable = False
            chk.reason = "zero effective width (beam on the slab edge?)"
            continue
        chk.beff = beff

        # ---- strengths -------------------------------------------------- #
        As = p["A"]
        full = composite_flexure(As, Fy, p["d"], p["bf"], p["tf"], p["tw"],
                                 t_sl, hr, fc, beff)
        chk.tc = full["tc"]
        chk.C_full = full["Cf"]
        chk.phiMn_full = phi_b * full["Mn"]
        st = stud_strength(stud_d, fc, stud_Fu)
        chk.Qn = st["Qn"]
        chk.n_studs = int(math.floor((span / 2.0) / rib_spacing))
        chk.sumQn = chk.n_studs * st["Qn"]
        chk.ratio_composite = chk.sumQn / full["Cf"]
        if chk.ratio_composite < 0.25:
            chk.notes.append("composite ratio < 0.25 (AISC I3.2d.1 user "
                             "note minimum)")
        part = composite_flexure(As, Fy, p["d"], p["bf"], p["tf"], p["tw"],
                                 t_sl, hr, fc, beff, sumQn=chk.sumQn)
        chk.pna = part["pna"]
        chk.phiMn_partial = phi_b * part["Mn"]
        chk.ratio = chk.Mu / chk.phiMn_partial

        # ---- pre-composite (unshored construction) --------------------- #
        if shored:
            chk.notes.append("shored construction: pre-composite check "
                             "skipped")
        elif dead_blk is None:
            chk.notes.append("no DEAD-classified case: pre-composite "
                             "check skipped")
        else:
            m_dead = _station_mu(dead_blk, m.uid)
            if m_dead is not None:
                chk.precomp_Mu = 1.4 * m_dead
                Sx = sec.I33 / (p["d"] / 2.0)
                Mn_steel, _ = _major_axis_capacity(
                    0.0, Fy, p["Zx"], Sx, p["ry"], p["rts"], sec.J,
                    p["d"] - p["tf"])          # Lb = 0: deck-braced flange
                chk.precomp_ratio = chk.precomp_Mu / (phi_b * Mn_steel)
                chk.notes.append("pre-composite: 1.4*Dead on the bare "
                                 "beam, Lb = 0 (deck braces the flange)")

        # ---- deflection (C-I3-4 equivalent inertia) --------------------- #
        chk.I_s = sec.I33
        mat = model.materials.get(sec.material)
        Es = float(mat.E) if mat is not None else E_STEEL
        Ec = 4700.0 * math.sqrt(fc / 1000.0) * 1000.0     # kPa
        chk.I_tr = transformed_inertia(As, sec.I33, p["d"], beff, t_sl,
                                       hr, Es / Ec)
        chk.I_equiv = equivalent_inertia(sec.I33, chk.I_tr, chk.sumQn,
                                         full["Cf"])
        if live_blk is None:
            chk.notes.append("no LIVE-classified case: deflection check "
                             "skipped")
        else:
            rel = _chord_relative_dy(live_blk, m.uid, span)
            if rel is not None:
                defl_steel = max(abs(v) for v in rel)
                chk.defl_LL = defl_steel * sec.I33 / chk.I_equiv
                chk.defl_limit = span / limit_den
                chk.defl_limit_ok = bool(
                    chk.defl_LL <= chk.defl_limit * (1.0 + 1e-12))

        # ---- camber recommendation (v0.23, industry rule) --------------- #
        # Pre-composite DL deflection = the DEAD case's chord-relative
        # bare-steel elastic line (the v0.16 recovery integrates the
        # member's own EI = Es*Is, so this IS the bare-Is deflection;
        # documented approximation: the analysis mesh may include the
        # slab, which slightly stiffens the END displacements — a true
        # staged wet-concrete model is out of scope).
        if dead_blk is not None:
            rel_d = _chord_relative_dy(dead_blk, m.uid, span)
            if rel_d is not None:
                chk.defl_DL = max(abs(v) for v in rel_d)
                chk.camber = camber_recommendation(chk.defl_DL, span)

        bad = chk.ratio > 1.0 or (chk.precomp_ratio is not None
                                  and chk.precomp_ratio > 1.0)
        chk.status = "NG" if bad else "OK"
    return checks


def summarize_composite(checks: List[CompositeBeamCheck]) -> dict:
    """Roll-up: counts, max ratio, governing beam."""
    ok = sum(1 for c in checks if c.status == "OK")
    ng = sum(1 for c in checks if c.status == "NG")
    na = sum(1 for c in checks if c.status == "N/A")
    governing = None
    max_ratio = 0.0
    for c in checks:
        if c.ratio is not None and c.ratio > max_ratio:
            max_ratio = c.ratio
            governing = c.uid
    return {"n": len(checks), "ok": ok, "ng": ng, "na": na,
            "max_ratio": max_ratio, "governing": governing,
            "preliminary": True}
