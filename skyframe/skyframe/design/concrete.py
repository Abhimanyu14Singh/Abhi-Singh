"""PRELIMINARY ACI 318-19 style checks for RECTANGULAR concrete members.

This module screens SkyFrame frame members (rectangular sections with the
drawing ``b``/``h`` dimensions set, e.g. from
:meth:`skyframe.core.model.FrameSection.rectangular`) against simplified
ACI 318-19 strength provisions:

* Beams, flexure  — singly-reinforced rectangular section strength
                    (Whitney stress block): Mn = As*fy*(d - a/2) with
                    a = As*fy/(0.85*fc'*b), checked separately for SAGGING
                    (bottom steel vs the max positive station M3) and
                    HOGGING (top steel vs the max negative station M3);
                    rho >= rho_min = max(0.25*sqrt(fc')/fy, 1.4/fy)
                    (fc', fy in MPa — ACI 9.6.1.2); the strength-reduction
                    factor follows the ACI 21.2.2 net-tensile-strain
                    interpolation (phi = 0.65 -> phi_flexure between
                    eps_ty and eps_ty + 0.003, eps_ty = fy/Es, Es = 200 GPa)
                    and a "not tension-controlled" note is emitted whenever
                    the section falls short of the tension-controlled limit.
* Beams, shear    — Vc = 0.17*lambda*sqrt(fc')*b*d (ACI 22.5.5.1, MPa
                    form, lambda = 1), Vs = Av*fy*d/s capped at
                    0.66*sqrt(fc')*b*d (ACI 22.5.1.2, noted when it
                    governs); ratio = Vu / (phi_shear*(Vc + Vs)) with
                    Vu = max |V2| over the member stations.
* Columns (v0.6)  — axial-flexure via a 5-point interaction diagram (pure
                    compression capped at phi*Pn,max = 0.80*phi*(0.85*fc'*
                    (Ag - Ast) + fy*Ast) for TIED columns; the eps_t = 0
                    decompression point; the balanced point from strain
                    compatibility; pure bending; pure tension) with LINEAR
                    interpolation between the points.  This is an
                    APPROXIMATE interaction — exact strain compatibility
                    holds only at the computed points, displaced concrete
                    at the compression steel is ignored, and only UNIAXIAL
                    bending about the section's major (local 3) axis is
                    considered.  The demand point (Pu, Mu) is rated by
                    RADIAL scaling from the origin to the diagram boundary.
                    Symmetric layouts only (n_top == n_bot is enforced;
                    unsymmetric layouts return "N/A" with a note).

THESE ARE PRELIMINARY SCREENING CHECKS, NOT A FINAL CODE CHECK.  Not
covered: slenderness/second-order effects, torsion, biaxial bending,
development/anchorage, detailing (bar spacing, max spacing of stirrups,
confinement), deep-beam provisions, deflections, crack control, seismic
provisions, and beam axial force (a note is emitted when a beam's axial
force exceeds the ACI 0.10*fc'*Ag "flexural member" limit).  Every result
carries ``"preliminary": True`` to make this explicit.

Units: SkyFrame SI throughout — kN, m, kPa (so fc' = 30 MPa is passed as
30_000.0 kPa and fy = 420 MPa as 420_000.0 kPa).  All kPa -> MPa
conversions required by the ACI square-root expressions are written
explicitly at the point of use.

Sign conventions (verified empirically against the OpenSees engine — see
tests/test_concrete_design.py and tests/test_design.py): station "M3"
values are POSITIVE for sagging (tension on the BOTTOM face — a gravity
beam shows positive M3 at midspan and negative M3 at fixed ends), and the
engine's ``member_forces[uid][0]`` (local N at end i) is POSITIVE for a
member in compression, so ``Pu`` here is +compression / -tension.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

__all__ = [
    "RebarLayout", "ConcreteCheck", "check_concrete_members",
    "check_concrete_members_envelope", "summarize",
    "ES_REBAR", "PHI_COMPRESSION", "beta1", "rho_min", "phi_from_strain",
    "beam_flexure", "beam_shear", "column_interaction",
]

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
ES_REBAR = 200_000_000.0    # kPa (200 GPa) — reinforcement modulus (ACI 20.2.2.2)
PHI_COMPRESSION = 0.65      # compression-controlled phi, TIED columns (ACI 21.2.2)
EPS_CU = 0.003              # concrete crushing strain (ACI 22.2.2.1)


# --------------------------------------------------------------------------- #
# reinforcement layout
# --------------------------------------------------------------------------- #
@dataclass
class RebarLayout:
    """Rebar for one rectangular member (single layer per face).

    All lengths in metres, stresses in kPa.  ``cover`` is the CLEAR cover
    to the stirrup face, so the effective depth to the bar layer centroid
    is ``d = h - cover - stirrup_dia - bar_dia/2``.
    """

    n_top: int                       # bars in the top layer
    n_bot: int                       # bars in the bottom layer
    bar_dia: float                   # m, longitudinal bar diameter
    cover: float = 0.04              # m, clear cover to the stirrup
    fy: float = 420_000.0            # kPa (420 MPa, Grade 60 metric)
    stirrup_dia: float = 0.010       # m
    stirrup_spacing: float = 0.15    # m
    stirrup_legs: int = 2

    def bar_area(self) -> float:
        """Area of ONE longitudinal bar [m^2]."""
        return math.pi * self.bar_dia ** 2 / 4.0

    def Av(self) -> float:
        """Total stirrup area per set, all legs [m^2]."""
        return self.stirrup_legs * math.pi * self.stirrup_dia ** 2 / 4.0

    def d_eff(self, h: float) -> float:
        """Effective depth to the tension-bar layer centroid [m]."""
        return h - self.cover - self.stirrup_dia - self.bar_dia / 2.0

    def to_dict(self) -> dict:
        return {"n_top": self.n_top, "n_bot": self.n_bot,
                "bar_dia": self.bar_dia, "cover": self.cover, "fy": self.fy,
                "stirrup_dia": self.stirrup_dia,
                "stirrup_spacing": self.stirrup_spacing,
                "stirrup_legs": self.stirrup_legs}


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #
@dataclass
class ConcreteCheck:
    """Outcome of one preliminary concrete member check (SI: kN, kN*m)."""

    uid: str
    section: str
    kind: str                            # "column" | "beam" | "brace"
    Pu: float = 0.0                      # kN, +compression / -tension
    Mu_pos: float = 0.0                  # kN*m, sagging demand (beams)
    Mu_neg: float = 0.0                  # kN*m, hogging demand (beams)
    Mu: float = 0.0                      # kN*m, max |M3| (columns)
    Vu: float = 0.0                      # kN, max |V2| (beams)
    phiMn_pos: Optional[float] = None    # kN*m (bottom steel, sagging)
    phiMn_neg: Optional[float] = None    # kN*m (top steel, hogging)
    phiVn: Optional[float] = None        # kN
    pm_points: Optional[List[Tuple[float, float]]] = None
    #                                    # [(phiMn, phiPn), ...] diagram (columns)
    ratio: Optional[float] = None        # governing demand/capacity
    equation: str = ""                   # "flexure(+)"|"flexure(-)"|"shear"|"P-M"
    status: str = "N/A"                  # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True             # ALWAYS True — screening check only
    governing_combo: str = ""            # v0.9 envelope: governing combo name

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "section": self.section, "kind": self.kind,
            "Pu": self.Pu, "Mu_pos": self.Mu_pos, "Mu_neg": self.Mu_neg,
            "Mu": self.Mu, "Vu": self.Vu,
            "phiMn_pos": self.phiMn_pos, "phiMn_neg": self.phiMn_neg,
            "phiVn": self.phiVn,
            "pm_points": ([[m, p] for m, p in self.pm_points]
                          if self.pm_points is not None else None),
            "ratio": self.ratio, "equation": self.equation,
            "status": self.status, "notes": list(self.notes),
            "preliminary": True,
            "governing_combo": self.governing_combo,
        }


# --------------------------------------------------------------------------- #
# capacity building blocks (module-level so tests can exercise them directly)
# --------------------------------------------------------------------------- #
def beta1(fc: float) -> float:
    """ACI 22.2.2.4.3 stress-block factor.  ``fc`` in kPa.

    beta1 = 0.85 for fc' <= 28 MPa, reduced by 0.05 per 7 MPa above
    28 MPa, not less than 0.65.
    """
    fc_MPa = fc / 1000.0                       # kPa -> MPa (explicit)
    if fc_MPa <= 28.0:
        return 0.85
    return max(0.65, 0.85 - 0.05 * (fc_MPa - 28.0) / 7.0)


def rho_min(fc: float, fy: float) -> float:
    """ACI 9.6.1.2 minimum flexural reinforcement ratio (As_min / (b*d)).

    rho_min = max(0.25*sqrt(fc') / fy, 1.4 / fy) with fc' and fy in MPa.
    ``fc``/``fy`` are passed in kPa and converted explicitly.
    """
    fc_MPa = fc / 1000.0                       # kPa -> MPa
    fy_MPa = fy / 1000.0                       # kPa -> MPa
    return max(0.25 * math.sqrt(fc_MPa) / fy_MPa, 1.4 / fy_MPa)


def phi_from_strain(eps_t: float, eps_ty: float,
                    phi_tc: float = 0.9) -> float:
    """ACI 21.2.2 strength-reduction factor from the net tensile strain.

    Compression-controlled (eps_t <= eps_ty): PHI_COMPRESSION (0.65, tied).
    Tension-controlled (eps_t >= eps_ty + 0.003): ``phi_tc``.
    In between: LINEAR interpolation
    phi = 0.65 + (phi_tc - 0.65) * (eps_t - eps_ty) / 0.003.
    """
    if eps_t <= eps_ty:
        return PHI_COMPRESSION
    if eps_t >= eps_ty + EPS_CU:
        return phi_tc
    return PHI_COMPRESSION + (phi_tc - PHI_COMPRESSION) \
        * (eps_t - eps_ty) / EPS_CU


def beam_flexure(b: float, d: float, As: float, fc: float, fy: float, *,
                 phi_tc: float = 0.9) -> dict:
    """Singly-reinforced rectangular flexural strength (one face).

    Whitney block:  a = As*fy / (0.85*fc'*b)   [kN / (kPa*m) = m]
                    Mn = As*fy * (d - a/2)     [kN*m]
    Net tensile strain from c = a/beta1 and dt = d (single bar layer):
    eps_t = 0.003*(dt - c)/c;  phi per :func:`phi_from_strain`.

    Returns a dict with a, c, beta1, eps_t, eps_ty, phi, Mn, phiMn,
    tension_controlled, steel_yields, rho, rho_min.
    """
    b1 = beta1(fc)
    T = As * fy                                # kN (m^2 * kPa)
    a = T / (0.85 * fc * b)                    # m
    c = a / b1                                 # m, neutral-axis depth
    eps_ty = fy / ES_REBAR                     # yield strain (kPa / kPa)
    eps_t = EPS_CU * (d - c) / c               # net tensile strain at dt = d
    phi = phi_from_strain(eps_t, eps_ty, phi_tc)
    Mn = T * (d - a / 2.0)                     # kN*m
    return {
        "a": a, "c": c, "beta1": b1, "eps_t": eps_t, "eps_ty": eps_ty,
        "phi": phi, "Mn": Mn, "phiMn": phi * Mn,
        "tension_controlled": eps_t >= eps_ty + EPS_CU,
        "steel_yields": eps_t >= eps_ty,
        "rho": As / (b * d), "rho_min": rho_min(fc, fy),
    }


def beam_shear(b: float, d: float, Av: float, s: float, fc: float,
               fy: float, *, phi_shear: float = 0.75) -> dict:
    """ACI one-way shear strength Vc + Vs [kN].

    Vc = 0.17*lambda*sqrt(fc')*b*d with lambda = 1 and fc' in MPa: the
    stress 0.17*sqrt(fc'[MPa]) is in MPa and is converted to kPa (x1000)
    so that stress[kPa] * area[m^2] = kN.  Vs = Av*fy*d/s (fy in kPa),
    capped at 0.66*sqrt(fc')*b*d (same MPa -> kPa conversion).
    """
    fc_MPa = fc / 1000.0                            # kPa -> MPa
    Vc = 0.17 * 1.0 * math.sqrt(fc_MPa) * 1000.0 * b * d    # MPa->kPa, ->kN
    Vs = Av * fy * d / s                            # kN
    Vs_cap = 0.66 * math.sqrt(fc_MPa) * 1000.0 * b * d      # kN
    capped = Vs > Vs_cap
    if capped:
        Vs = Vs_cap
    Vn = Vc + Vs
    return {"Vc": Vc, "Vs": Vs, "Vs_cap": Vs_cap, "Vs_capped": capped,
            "Vn": Vn, "phiVn": phi_shear * Vn}


def _column_section_forces(c: float, b: float, h: float, d: float,
                           dp: float, As_t: float, As_c: float, fc: float,
                           fy: float) -> Tuple[float, float, float]:
    """(Pn, Mn, eps_t) for one neutral-axis depth c (strain compatibility).

    Compression fibre at the top; ``d`` = depth to the tension steel,
    ``dp`` = depth to the compression steel.  Pn positive in compression,
    Mn about the section mid-height.  Displaced concrete at the
    compression steel is ignored (documented approximation).
    """
    b1 = beta1(fc)
    a = min(b1 * c, h)
    Cc = 0.85 * fc * a * b                              # kN
    eps_c = EPS_CU * (c - dp) / c                       # comp.-steel strain
    fs_c = max(-fy, min(fy, ES_REBAR * eps_c))          # kPa, +compression
    eps_t = EPS_CU * (d - c) / c                        # tension-steel strain
    fs_t = max(-fy, min(fy, ES_REBAR * eps_t))          # kPa, +tension
    Pn = Cc + As_c * fs_c - As_t * fs_t                 # kN
    Mn = Cc * (h / 2.0 - a / 2.0) + As_c * fs_c * (h / 2.0 - dp) \
        + As_t * fs_t * (d - h / 2.0)                   # kN*m
    return Pn, Mn, eps_t


def column_interaction(b: float, h: float, d: float, dp: float,
                       As_face: float, fc: float, fy: float, *,
                       phi_tc: float = 0.9) -> List[dict]:
    """5-point uniaxial P-M interaction diagram for a symmetric TIED column.

    Points (each a dict with label, c, Pn, Mn, eps_t, phi, phiPn, phiMn):

    1. ``"pure compression"`` — Pn0 = 0.85*fc'*(Ag - Ast) + fy*Ast with the
       ACI 22.4.2 tied-column cap ALREADY applied on the design value:
       phiPn = 0.80*PHI_COMPRESSION*Pn0 (Mn = 0).
    2. ``"eps_t = 0"``        — strain compatibility at c = d (tension steel
       at zero strain; compression-controlled, phi = 0.65).
    3. ``"balanced"``         — strain compatibility at
       c_b = d * 0.003/(0.003 + eps_ty), eps_ty = fy/Es (phi = 0.65).
    4. ``"pure bending"``     — c solved by bisection for Pn = 0; phi from
       the resulting net tensile strain.
    5. ``"pure tension"``     — Pn = -fy*Ast, Mn = 0, phi = phi_tc.

    phiPn of every point is capped at the point-1 value (0.80*phi*Pn0).
    Exact strain compatibility holds ONLY at these points; the diagram is
    interpolated linearly between them (documented approximation).
    """
    Ag = b * h
    Ast = 2.0 * As_face
    eps_ty = fy / ES_REBAR
    pts: List[dict] = []

    # 1 — pure compression (ACI 22.4.2.2 / 22.4.2.4, tied)
    Pn0 = 0.85 * fc * (Ag - Ast) + fy * Ast
    phiPn_max = 0.80 * PHI_COMPRESSION * Pn0
    pts.append({"label": "pure compression", "c": math.inf, "Pn": 0.80 * Pn0,
                "Mn": 0.0, "eps_t": -EPS_CU, "phi": PHI_COMPRESSION,
                "phiPn": phiPn_max, "phiMn": 0.0})

    # 2 — eps_t = 0 (decompression of the tension steel), c = d
    # 3 — balanced, c_b from strain compatibility
    for label, c in (("eps_t = 0", d),
                     ("balanced", d * EPS_CU / (EPS_CU + eps_ty))):
        Pn, Mn, eps_t = _column_section_forces(c, b, h, d, dp, As_face,
                                               As_face, fc, fy)
        phi = phi_from_strain(eps_t, eps_ty, phi_tc)
        pts.append({"label": label, "c": c, "Pn": Pn, "Mn": Mn,
                    "eps_t": eps_t, "phi": phi,
                    "phiPn": min(phi * Pn, phiPn_max), "phiMn": phi * Mn})

    # 4 — pure bending: bisect c in (0, d] for Pn = 0 (Pn is monotonic in c)
    lo, hi = 1e-6 * h, d
    Pn_lo = _column_section_forces(lo, b, h, d, dp, As_face, As_face,
                                   fc, fy)[0]
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        Pn_mid = _column_section_forces(mid, b, h, d, dp, As_face, As_face,
                                        fc, fy)[0]
        if (Pn_lo < 0.0) == (Pn_mid < 0.0):
            lo, Pn_lo = mid, Pn_mid
        else:
            hi = mid
    c0 = 0.5 * (lo + hi)
    Pn, Mn, eps_t = _column_section_forces(c0, b, h, d, dp, As_face,
                                           As_face, fc, fy)
    phi = phi_from_strain(eps_t, eps_ty, phi_tc)
    pts.append({"label": "pure bending", "c": c0, "Pn": Pn, "Mn": Mn,
                "eps_t": eps_t, "phi": phi, "phiPn": phi * Pn,
                "phiMn": phi * Mn})

    # 5 — pure tension
    pts.append({"label": "pure tension", "c": 0.0, "Pn": -fy * Ast,
                "Mn": 0.0, "eps_t": math.inf, "phi": phi_tc,
                "phiPn": -phi_tc * fy * Ast, "phiMn": 0.0})
    return pts


def _radial_ratio(points: List[Tuple[float, float]],
                  Mu: float, Pu: float) -> Optional[float]:
    """Demand/capacity ratio by RADIAL scaling from the (0, 0) origin.

    ``points`` is the design-diagram polyline [(phiMn, phiPn), ...] from
    pure compression (M = 0, P > 0) down to pure tension (M = 0, P < 0);
    the origin lies inside the closed region (the closure runs along the
    M = 0 axis), so a ray through the demand point crosses the polyline
    exactly once.  ratio = |demand| / |boundary point along the ray|.
    """
    if Mu == 0.0 and Pu == 0.0:
        return 0.0
    best_s: Optional[float] = None
    for (M1, P1), (M2, P2) in zip(points[:-1], points[1:]):
        dM, dP = M2 - M1, P2 - P1
        det = Mu * dP - Pu * dM              # solve t*(seg) - s*(ray) = -p1
        if abs(det) < 1e-14 * max(1.0, abs(Mu) + abs(Pu)):
            continue                         # ray parallel to segment
        t = (Mu * P1 - Pu * M1) / -det
        s = (dM * P1 - dP * M1) / -det
        if -1e-9 <= t <= 1.0 + 1e-9 and s > 1e-12:
            if best_s is None or s < best_s:
                best_s = s
    return None if best_s is None else 1.0 / best_s


# --------------------------------------------------------------------------- #
# demand extraction
# --------------------------------------------------------------------------- #
def _case_block(results, case_or_combo: str) -> dict:
    """The per-case results dict for a case, combo, or RS case name.

    Accepts a results OBJECT (anything with .to_dict()) or an already
    JSON-shaped dict per CONTRACT.md (same behavior as design.steel).
    """
    d = results.to_dict() if hasattr(results, "to_dict") else results
    if not isinstance(d, dict):
        raise TypeError("results must be a results object or a results dict")
    for key in ("cases", "combos", "rs_cases"):
        block = d.get(key) or {}
        if case_or_combo in block:
            return block[case_or_combo]
    raise KeyError(f"case/combo {case_or_combo!r} not found in results "
                   "(looked in 'cases', 'combos', 'rs_cases')")


def _beam_demands(case: dict, uid: str, notes: List[str]):
    """(Pu, Mu_pos, Mu_neg, Vu) for a beam, or None if it has no forces.

    Station M3 is signed: positive = SAGGING (tension bottom), negative =
    HOGGING (tension top) — engine convention verified in the tests.
    Without stations the two end |M3| values are used for BOTH faces
    (their sign convention differs end-to-end) with a note.
    """
    mf = (case.get("member_forces") or {}).get(uid)
    if mf is None:
        return None
    Pu = float(mf[0])
    st = (case.get("member_stations") or {}).get(uid)
    if st and st.get("M3"):
        m3 = [float(v) for v in st["M3"]]
        Mu_pos = max(0.0, max(m3))
        Mu_neg = max(0.0, -min(m3))
        Vu = max(abs(float(v)) for v in st.get("V2") or [0.0])
    else:
        notes.append("no station results — end |M3| applied to BOTH faces, "
                     "end |V2| used for shear")
        m_end = max(abs(float(mf[5])), abs(float(mf[11])))
        Mu_pos = Mu_neg = m_end
        Vu = max(abs(float(mf[1])), abs(float(mf[7])))
    return Pu, Mu_pos, Mu_neg, Vu


def _column_demands(case: dict, uid: str):
    """(Pu, Mu) for a column, or None.  Pu = +compression (end-i local N);
    Mu = max |M3| over stations (else over the two ends)."""
    mf = (case.get("member_forces") or {}).get(uid)
    if mf is None:
        return None
    Pu = float(mf[0])
    st = (case.get("member_stations") or {}).get(uid)
    if st and st.get("M3"):
        Mu = max(abs(float(v)) for v in st["M3"])
    else:
        Mu = max(abs(float(mf[5])), abs(float(mf[11])))
    return Pu, Mu


# --------------------------------------------------------------------------- #
# per-member checks
# --------------------------------------------------------------------------- #
def _check_beam(chk: ConcreteCheck, case: dict, b: float, h: float,
                lay: RebarLayout, fc: float, phi_flexure: float,
                phi_shear: float) -> None:
    dem = _beam_demands(case, chk.uid, chk.notes)
    if dem is None:
        chk.notes.append("no member forces in results for this member")
        return
    chk.Pu, chk.Mu_pos, chk.Mu_neg, chk.Vu = dem
    d = lay.d_eff(h)
    if d <= 0.0:
        chk.notes.append(f"effective depth d = {d:.3f} m <= 0 "
                         "(cover/stirrup/bar too large for h)")
        return
    if abs(chk.Pu) > 0.10 * fc * b * h:
        chk.notes.append("|Pu| exceeds 0.10*fc'*Ag — beam flexure check "
                         "ignores axial force (preliminary)")

    ratios: List[Tuple[float, str]] = []
    ok = True
    # (face label, demand, bar count, capacity attribute)
    for face, Mu, n, attr in (("flexure(+)", chk.Mu_pos, lay.n_bot,
                               "phiMn_pos"),
                              ("flexure(-)", chk.Mu_neg, lay.n_top,
                               "phiMn_neg")):
        if n <= 0:
            if Mu > 1e-9:
                chk.notes.append(f"{face}: demand {Mu:.3g} kN*m but no "
                                 "bars on the tension face")
                ok = False
            continue
        As = n * lay.bar_area()
        fx = beam_flexure(b, d, As, fc, lay.fy, phi_tc=phi_flexure)
        setattr(chk, attr, fx["phiMn"])
        if not fx["tension_controlled"]:
            chk.notes.append(
                f"{face}: not tension-controlled (eps_t = {fx['eps_t']:.4f}"
                f" < {fx['eps_ty'] + EPS_CU:.4f}) — phi reduced to "
                f"{fx['phi']:.3f} per ACI 21.2.2")
        if not fx["steel_yields"]:
            chk.notes.append(f"{face}: eps_t < eps_ty — tension steel does "
                             "not yield; Whitney-block Mn is approximate")
        if fx["rho"] < fx["rho_min"]:
            chk.notes.append(f"{face}: rho = {fx['rho']:.5f} < rho_min = "
                             f"{fx['rho_min']:.5f} (ACI 9.6.1.2)")
            ok = False
        ratios.append((Mu / fx["phiMn"], face))

    sh = beam_shear(b, d, lay.Av(), lay.stirrup_spacing, fc, lay.fy,
                    phi_shear=phi_shear)
    chk.phiVn = sh["phiVn"]
    if sh["Vs_capped"]:
        chk.notes.append("Vs capped at 0.66*sqrt(fc')*b*d (ACI 22.5.1.2)")
    ratios.append((chk.Vu / sh["phiVn"], "shear"))

    chk.ratio, chk.equation = max(ratios)
    chk.status = "OK" if (chk.ratio <= 1.0 and ok) else "NG"


def _check_column(chk: ConcreteCheck, case: dict, b: float, h: float,
                  lay: RebarLayout, fc: float, phi_flexure: float) -> None:
    if lay.n_top != lay.n_bot:
        chk.notes.append(f"unsymmetric layout (n_top = {lay.n_top} != "
                         f"n_bot = {lay.n_bot}) not supported for the v0.6 "
                         "column interaction — not checked")
        return
    dem = _column_demands(case, chk.uid)
    if dem is None:
        chk.notes.append("no member forces in results for this member")
        return
    chk.Pu, chk.Mu = dem
    d = lay.d_eff(h)
    dp = lay.cover + lay.stirrup_dia + lay.bar_dia / 2.0
    if d <= dp:
        chk.notes.append(f"effective depth d = {d:.3f} m <= d' = {dp:.3f} m"
                         " (cover/stirrup/bar too large for h)")
        return
    pts = column_interaction(b, h, d, dp, lay.n_top * lay.bar_area(), fc,
                             lay.fy, phi_tc=phi_flexure)
    chk.pm_points = [(p["phiMn"], p["phiPn"]) for p in pts]
    chk.notes.append("approximate 5-point interaction, uniaxial about the "
                     "major (local 3) axis; M2 not considered (v0.6)")
    ratio = _radial_ratio(chk.pm_points, chk.Mu, chk.Pu)
    if ratio is None:                          # defensive; should not happen
        chk.notes.append("interaction ratio could not be computed")
        return
    chk.ratio, chk.equation = ratio, "P-M"
    chk.status = "OK" if ratio <= 1.0 else "NG"


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_concrete_members(model, results, case_or_combo: str,
                           rebar: Dict[str, RebarLayout], *,
                           fc: float = 30_000.0, phi_flexure: float = 0.9,
                           phi_shear: float = 0.75) -> List[ConcreteCheck]:
    """Preliminary ACI 318-19 checks for rectangular concrete members.

    Parameters
    ----------
    model         : BuildingModel the results were computed from.
    results       : engine results object (with .to_dict()) or the
                    CONTRACT.md results dict; may also be a hand-built dict
                    with a ``cases`` block (no engine needed).
    case_or_combo : name of the case / combo / RS case to check.
    rebar         : member uid -> :class:`RebarLayout`.  Members without an
                    entry return status "N/A" with a note.
    fc            : concrete cylinder strength fc' [kPa]; default 30 MPa.
    phi_flexure   : tension-controlled flexural resistance factor (the phi
                    is REDUCED toward 0.65 per ACI 21.2.2 when a section is
                    not tension-controlled).
    phi_shear     : shear resistance factor.

    Beams get sagging/hogging flexure + shear checks; columns get the
    approximate uniaxial P-M interaction check (see module docstring).
    Braces and members whose section carries no rectangular ``b``/``h``
    drawing dimensions return "N/A".  Results are PRELIMINARY.
    """
    case = _case_block(results, case_or_combo)
    checks: List[ConcreteCheck] = []
    for m in model.members:
        chk = ConcreteCheck(uid=m.uid, section=m.section, kind=m.kind)
        checks.append(chk)

        sec = model.sections.get(m.section)
        if sec is None:
            chk.notes.append(f"unknown model section {m.section!r}")
            continue
        b, h = float(sec.b), float(sec.h)
        if b <= 0.0 or h <= 0.0:
            chk.notes.append("section has no rectangular b/h dimensions — "
                             "not checked")
            continue
        lay = rebar.get(m.uid)
        if lay is None:
            chk.notes.append("no rebar layout provided for this member")
            continue
        bad = _validate_layout(lay)
        if bad:
            chk.notes.append(bad)
            continue
        if m.kind == "beam":
            _check_beam(chk, case, b, h, lay, fc, phi_flexure, phi_shear)
        elif m.kind == "column":
            _check_column(chk, case, b, h, lay, fc, phi_flexure)
        else:
            chk.notes.append(f"kind {m.kind!r} not checked (v0.6 concrete "
                             "scope is beams and columns)")
    return checks


def _validate_layout(lay: RebarLayout) -> Optional[str]:
    """Reason string when a layout is unusable, else None."""
    if lay.n_top < 0 or lay.n_bot < 0 or lay.n_top + lay.n_bot == 0:
        return (f"invalid bar counts n_top = {lay.n_top}, "
                f"n_bot = {lay.n_bot}")
    for name in ("bar_dia", "cover", "fy", "stirrup_dia", "stirrup_spacing"):
        v = getattr(lay, name)
        if not (isinstance(v, (int, float)) and math.isfinite(v)
                and v > 0.0):
            return f"invalid rebar layout: {name} must be > 0 (got {v!r})"
    if lay.stirrup_legs < 1:
        return f"invalid rebar layout: stirrup_legs = {lay.stirrup_legs}"
    return None


def check_concrete_members_envelope(
        model, results, rebar: Dict[str, RebarLayout],
        combos: Optional[List[str]] = None, **kw) -> List[ConcreteCheck]:
    """Governing ACI 318 concrete checks over a set of load combinations.

    Runs :func:`check_concrete_members` for every combo in ``combos``
    (default: every name in ``results['combos']``) and returns, per member,
    the check with the LARGEST demand/capacity ratio, tagged with
    ``governing_combo``.  A member that is "N/A" in every combo keeps the
    first combo's check.  ``**kw`` is forwarded (fc, phi_flexure, phi_shear).
    A single-combo envelope equals that combo's checks (plus the tag).
    """
    d = results.to_dict() if hasattr(results, "to_dict") else results
    if combos is None:
        combos = list((d.get("combos") or {}).keys())
    if not combos:
        raise ValueError("check_concrete_members_envelope: no combos to "
                         "envelope (none supplied and results has no "
                         "'combos')")
    per_combo = {c: check_concrete_members(model, d, c, rebar, **kw)
                 for c in combos}
    n = len(per_combo[combos[0]])
    out: List[ConcreteCheck] = []
    for i in range(n):
        gov = per_combo[combos[0]][i]
        gov_combo = combos[0]

        def rr(chk: ConcreteCheck) -> float:
            return chk.ratio if chk.ratio is not None else -math.inf

        for c in combos[1:]:
            chk = per_combo[c][i]
            if rr(chk) > rr(gov):
                gov, gov_combo = chk, c
        gov.governing_combo = gov_combo
        out.append(gov)
    return out


def summarize(checks: List[ConcreteCheck]) -> dict:
    """Roll-up of a check run: counts, max ratio, governing member."""
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
