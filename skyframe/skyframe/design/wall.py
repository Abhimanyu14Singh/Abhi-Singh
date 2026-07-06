"""PRELIMINARY ACI 318-14/19 style shear wall (pier) design checks (v0.18).

Checks every pier-labeled wall stack of a SkyFrame model against three
uniform-reinforcing pier provisions, driven by the EXISTING per-story pier
P/V/M design forces (the v0.15 exact free-body recovery,
``results["piers"]``):

* PMM        — in-plane P-M interaction of the rectangular pier section
               Lw x t with UNIFORMLY DISTRIBUTED vertical reinforcement
               (ratio ``rho_v``): strip strain-compatibility integration
               (``N_STRIPS`` = 240 midpoint strips >= the 200 the spec
               requires) with the ACI rectangular stress block (a =
               beta1*c per ACI Table 22.2.2.4.3), phi per the ACI 21.2
               net-tensile-strain transition evaluated at the EXTREME bar
               (the outermost strip centroid), and the design polyline
               rated by RADIAL scaling in (M, P) space exactly like
               :mod:`skyframe.design.concrete` rates columns (its
               ``_radial_ratio`` helper is reused).  IN-PLANE bending only
               (M about the wall's strong axis); out-of-plane bending of
               wall piers is OUT OF SCOPE in v0.18.
* Shear      — ACI 318-19 Eq. 11.5.4.3 (== 18.10.4.1 for walls):
               ``Vn = Acv*(alpha_c*lambda*sqrt(fc') + rho_t*fy)`` with the
               psi-unit coefficient ``alpha_c`` = 3.0 for hw/lw <= 1.5,
               2.0 for hw/lw >= 2.0, linear between; worked in SI as
               ``alpha_c * 0.083 * sqrt(fc'[MPa])`` MPa (0.083 MPa =
               1.0 psi-sqrt coefficient, the standard SI transcription);
               ``Acv = Lw*t``, ``rho_t = rho_h``, ``phi_v = 0.75``, and the
               §18.10.4.4 upper bound ``Vn <= 0.66*sqrt(fc'[MPa])*Acv``.
               ``hw`` is the TOTAL wall height of the pier stack (z extent
               of every wall region sharing the pier label).
* Boundary   — ACI §18.10.6.3 stress-based boundary-element trigger:
               extreme-fiber compressive stress
               ``sigma = P/Ag + |M|*(Lw/2)/Ig`` (gross section,
               ``Ig = t*Lw^3/12``) under each combo; boundary elements are
               REQUIRED where ``sigma > 0.2*fc'``.  Reported per story with
               the governing (max) stress and its combo.

fc' comes from an explicit ``fc_prime`` (kPa) when supplied; otherwise it
is DERIVED from the wall shell section's concrete modulus by inverting ACI
19.2.2.1 ``Ec = 4700*sqrt(fc')`` (MPa units): ``fc' = (E[MPa]/4700)^2``
(:func:`fc_from_E` — documented approximation; SkyFrame materials store E,
not fc').  Reinforcement: ``rho_v``/``rho_h`` default 0.0025 (the ACI
11.6.1 minimum web ratios) and ``fy`` defaults to 420 MPa = 420_000 kPa.

THESE ARE PRELIMINARY SCREENING CHECKS, NOT A FINAL CODE CHECK.  Not
covered: out-of-plane P-M, slenderness (14.5/11.8), sliding shear,
displacement-based boundary elements (18.10.6.2), boundary detailing,
coupling beams, openings inside a pier, and bar-cutoff/development.  Every
result carries ``"preliminary": True``.

Units: SkyFrame SI throughout — kN, m, kPa (fc' = 30 MPa is passed as
30_000.0 kPa, fy = 420 MPa as 420_000.0 kPa).  Every kPa <-> MPa conversion
demanded by an ACI square-root expression is written explicitly at the
point of use.

Sign conventions (CONTRACT v0.15): pier ``P`` is POSITIVE IN COMPRESSION,
``V``/``M`` are signed in-plane resultants — the section and its uniform
reinforcement are symmetric, so the checks use |V| and |M|.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from skyframe.design.concrete import (EPS_CU, ES_REBAR, PHI_COMPRESSION,
                                      _radial_ratio, beta1, phi_from_strain)

__all__ = [
    "WallPierCheck", "check_wall_piers", "summarize_walls",
    "wall_interaction", "wall_section_forces", "wall_shear_strength",
    "boundary_element_check", "fc_from_E", "pier_geometry",
    "N_STRIPS", "PHI_SHEAR_WALL", "BOUNDARY_STRESS_FRACTION",
]

# strip count for the uniform-reinforcement strain-compatibility
# integration (midpoint rule; the spec floor is 200 — 240 keeps the
# worst-case kink-cell error comfortably under the 1e-3 test tolerance)
N_STRIPS = 240
PHI_SHEAR_WALL = 0.75              # ACI 21.2.1 shear phi
BOUNDARY_STRESS_FRACTION = 0.20    # ACI 18.10.6.3 sigma > 0.2 fc' trigger

# c/Lw sweep for the interaction polyline (descending -> P descends from
# pure compression toward pure bending; balanced + the bisected Pn = 0
# point are inserted, pure tension appended)
_C_SWEEP = (4.0, 3.0, 2.5, 2.0, 1.6, 1.3, 1.1, 0.95, 0.85, 0.75, 0.65,
            0.575, 0.5, 0.45, 0.4, 0.36, 0.32, 0.28, 0.25, 0.22, 0.19,
            0.16, 0.14, 0.12, 0.10, 0.08, 0.06, 0.045, 0.03, 0.02)


def fc_from_E(E: float) -> float:
    """fc' (kPa) implied by a concrete modulus ``E`` (kPa) — ACI 19.2.2.1.

    ``Ec = 4700*sqrt(fc')`` in MPa units, inverted:
    ``fc'[MPa] = (E[MPa]/4700)^2``; both conversions explicit:
    E kPa -> MPa (/1000), result MPa -> kPa (*1000).
    """
    E_MPa = E / 1000.0                       # kPa -> MPa
    return (E_MPa / 4700.0) ** 2 * 1000.0    # MPa -> kPa


# --------------------------------------------------------------------------- #
# P-M interaction of a uniformly-reinforced rectangular wall section
# --------------------------------------------------------------------------- #
def wall_section_forces(c: float, Lw: float, t: float, rho_v: float,
                        fc: float, fy: float,
                        n_strips: int = N_STRIPS
                        ) -> Tuple[float, float, float]:
    """(Pn, Mn, eps_t) at neutral-axis depth ``c`` — strip integration.

    Rectangular section ``Lw x t`` bending IN PLANE about mid-length, the
    extreme COMPRESSION fibre at x = 0 and depth x measured toward the
    tension end.  Concrete: ACI rectangular stress block, uniform
    0.85*fc' over ``a = min(beta1*c, Lw)`` (width ``t``).  Steel: total
    ``Ast = rho_v*Lw*t`` split into ``n_strips`` equal strips at the strip
    CENTROIDS ``x_i = (i + 0.5)*Lw/n``; strain ``eps_i =
    EPS_CU*(c - x_i)/c`` (plane sections), stress clamped to +-fy.
    Displaced concrete at compression steel is ignored (the same
    documented approximation as :mod:`skyframe.design.concrete`).

    ``eps_t`` is the net tensile strain of the EXTREME bar, i.e. the
    outermost strip centroid at ``d_t = Lw*(1 - 0.5/n)`` (tension
    positive) — the strain entering the ACI 21.2 phi transition.
    Pn positive in compression (kN); Mn about mid-length (kN*m).
    """
    if c <= 0.0:
        raise ValueError(f"neutral-axis depth c must be > 0 (got {c})")
    b1 = beta1(fc)
    a = min(b1 * c, Lw)
    Cc = 0.85 * fc * a * t                          # kN (kPa * m^2)
    Pn = Cc
    Mn = Cc * (Lw / 2.0 - a / 2.0)                  # kN*m
    As_strip = rho_v * Lw * t / n_strips            # m^2 per strip
    dx = Lw / n_strips
    for i in range(n_strips):
        x = (i + 0.5) * dx
        eps = EPS_CU * (c - x) / c                  # +compression
        fs = max(-fy, min(fy, ES_REBAR * eps))      # kPa, +compression
        F = As_strip * fs                           # kN
        Pn += F
        Mn += F * (Lw / 2.0 - x)
    d_t = Lw * (1.0 - 0.5 / n_strips)
    eps_t = EPS_CU * (d_t - c) / c                  # +tension
    return Pn, Mn, eps_t


def wall_interaction(Lw: float, t: float, rho_v: float, fc: float,
                     fy: float, *, n_strips: int = N_STRIPS,
                     phi_tc: float = 0.9) -> List[dict]:
    """Design P-M interaction polyline of a uniformly-reinforced wall pier.

    Points (each ``{label, c, Pn, Mn, eps_t, phi, phiPn, phiMn}``) ordered
    from pure compression down to pure tension:

    1. ``"pure compression"`` — ``Pn0 = 0.85*fc'*(Ag - Ast) + fy*Ast``
       with the ACI 22.4.2.1 TIED cap already on the design value:
       ``phiPn_max = 0.80*PHI_COMPRESSION*Pn0`` (Mn = 0).
    2. A descending neutral-axis sweep ``c = f*Lw`` for f in ``_C_SWEEP``
       (strip strain compatibility, :func:`wall_section_forces`), the
       BALANCED point ``c_b = d_t*EPS_CU/(EPS_CU + fy/Es)`` inserted in
       order, phi per ACI 21.2 from the extreme-bar strain, every phiPn
       capped at the point-1 value.
    3. ``"pure bending"`` — c bisected for Pn = 0 (Pn is monotonic in c).
    4. ``"pure tension"``  — ``Pn = -fy*Ast``, Mn = 0 (uniform steel is
       symmetric about mid-length), phi = ``phi_tc``.
    """
    Ag = Lw * t
    Ast = rho_v * Ag
    eps_ty = fy / ES_REBAR
    d_t = Lw * (1.0 - 0.5 / n_strips)

    pts: List[dict] = []
    # 1 — pure compression (ACI 22.4.2.2 / 22.4.2.4, tied)
    Pn0 = 0.85 * fc * (Ag - Ast) + fy * Ast
    phiPn_max = 0.80 * PHI_COMPRESSION * Pn0
    pts.append({"label": "pure compression", "c": math.inf, "Pn": 0.80 * Pn0,
                "Mn": 0.0, "eps_t": -EPS_CU, "phi": PHI_COMPRESSION,
                "phiPn": phiPn_max, "phiMn": 0.0})

    # 3 — pure bending c first (so the sweep can keep points above it):
    # bisect c in (0, d_t] for Pn = 0 (Pn is monotonic increasing in c)
    lo, hi = 1e-6 * Lw, d_t
    Pn_lo = wall_section_forces(lo, Lw, t, rho_v, fc, fy, n_strips)[0]
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        Pn_mid = wall_section_forces(mid, Lw, t, rho_v, fc, fy, n_strips)[0]
        if (Pn_lo < 0.0) == (Pn_mid < 0.0):
            lo, Pn_lo = mid, Pn_mid
        else:
            hi = mid
    c0 = 0.5 * (lo + hi)

    # 2 — descending sweep + the balanced point, stopping at c0
    c_b = d_t * EPS_CU / (EPS_CU + eps_ty)
    cs = sorted({f * Lw for f in _C_SWEEP} | {c_b}, reverse=True)
    for c in cs:
        if c <= c0:
            continue                       # below pure bending: skip
        Pn, Mn, eps_t = wall_section_forces(c, Lw, t, rho_v, fc, fy,
                                            n_strips)
        phi = phi_from_strain(eps_t, eps_ty, phi_tc)
        pts.append({"label": ("balanced" if c == c_b else f"c={c:.4g}"),
                    "c": c, "Pn": Pn, "Mn": Mn, "eps_t": eps_t, "phi": phi,
                    "phiPn": min(phi * Pn, phiPn_max), "phiMn": phi * Mn})

    Pn, Mn, eps_t = wall_section_forces(c0, Lw, t, rho_v, fc, fy, n_strips)
    phi = phi_from_strain(eps_t, eps_ty, phi_tc)
    pts.append({"label": "pure bending", "c": c0, "Pn": Pn, "Mn": Mn,
                "eps_t": eps_t, "phi": phi, "phiPn": phi * Pn,
                "phiMn": phi * Mn})

    # 4 — pure tension
    pts.append({"label": "pure tension", "c": 0.0, "Pn": -fy * Ast,
                "Mn": 0.0, "eps_t": math.inf, "phi": phi_tc,
                "phiPn": -phi_tc * fy * Ast, "phiMn": 0.0})
    return pts


# --------------------------------------------------------------------------- #
# shear + boundary-element building blocks
# --------------------------------------------------------------------------- #
def wall_shear_strength(Lw: float, t: float, hw: float, rho_h: float,
                        fc: float, fy: float, *, lam: float = 1.0,
                        phi_shear: float = PHI_SHEAR_WALL) -> dict:
    """ACI 318-19 §11.5.4.3 in-plane wall shear strength (SI form).

    ``Vn = Acv*(alpha_c*lambda*sqrt(fc') + rho_t*fy)`` with
    ``alpha_c`` (psi units) = 3.0 for hw/lw <= 1.5, 2.0 for hw/lw >= 2.0,
    LINEAR between; the psi coefficient converts to SI as
    ``alpha_c * 0.083 * sqrt(fc'[MPa])`` MPa (1 sqrt-psi coefficient =
    0.083 sqrt-MPa).  Unit chain, fully explicit::

        vc [kPa] = alpha_c * 0.083 * lam * sqrt(fc/1000 [MPa]) * 1000
        vs [kPa] = rho_h * fy [kPa]              (rho_t*fy, already kPa)
        Vn [kN]  = Acv [m^2] * (vc + vs) [kPa]

    Upper bound (§18.10.4.4): ``Vn <= 0.66*sqrt(fc'[MPa])*Acv`` (the SI
    transcription of 8*sqrt(psi)); ``capped`` reports when it governs.
    ``phiVn = phi_shear * Vn`` with phi = 0.75 (ACI 21.2.1).
    """
    if Lw <= 0.0 or t <= 0.0:
        raise ValueError("wall_shear_strength: Lw and t must be > 0")
    Acv = Lw * t                                        # m^2
    r = hw / Lw
    if r <= 1.5:
        alpha_c = 3.0
    elif r >= 2.0:
        alpha_c = 2.0
    else:
        alpha_c = 3.0 - 2.0 * (r - 1.5)                 # linear 3.0 -> 2.0
    fc_MPa = fc / 1000.0                                # kPa -> MPa
    vc = alpha_c * 0.083 * lam * math.sqrt(fc_MPa) * 1000.0   # MPa -> kPa
    vs = rho_h * fy                                     # kPa
    Vn = Acv * (vc + vs)                                # kN
    Vn_cap = 0.66 * math.sqrt(fc_MPa) * 1000.0 * Acv    # kN (MPa -> kPa)
    capped = Vn > Vn_cap
    if capped:
        Vn = Vn_cap
    return {"Acv": Acv, "alpha_c": alpha_c, "hw_over_lw": r, "vc": vc,
            "vs": vs, "Vn": Vn, "Vn_cap": Vn_cap, "capped": capped,
            "phiVn": phi_shear * Vn}


def boundary_element_check(P: float, M: float, Lw: float, t: float,
                           fc: float) -> dict:
    """ACI §18.10.6.3 stress-based boundary-element trigger.

    Gross-section extreme-fibre compressive stress under (P, M):
    ``sigma = P/Ag + |M|*(Lw/2)/Ig`` with ``Ag = Lw*t`` and
    ``Ig = t*Lw^3/12`` (kPa; P +compression).  Boundary elements are
    REQUIRED when ``sigma > 0.2*fc'``.
    """
    Ag = Lw * t
    Ig = t * Lw ** 3 / 12.0
    sigma = P / Ag + abs(M) * (Lw / 2.0) / Ig           # kPa
    limit = BOUNDARY_STRESS_FRACTION * fc
    return {"sigma": sigma, "limit": limit, "required": sigma > limit}


# --------------------------------------------------------------------------- #
# pier geometry from the model
# --------------------------------------------------------------------------- #
def pier_geometry(model) -> Dict[str, dict]:
    """Per-pier geometry pulled from the labeled wall regions.

    Returns ``{label: {"t", "hw", "E", "stories": {story: {"Lw", "hs"}},
    "notes": [...]}}`` for every wall that reports pier forces (explicit
    ``pier`` label, or ``auto_pier_walls`` labeling with the uid — the
    same rule as the engine's ``_pier_regions``).

    * ``Lw`` — the wall's horizontal in-plane extent (max - min of the
      corner projections onto ``hhat = ez x nhat``); walls sharing a label
      have their extents SUMMED per story (they are one pier per the
      v0.15 contract).  Constant over height per region (rectangular
      walls; the corner extent is used for all stories — documented
      approximation for tapered regions).
    * A story is covered when the wall's z range overlaps it (the engine's
      pier-cut rule); ``hs`` is that story's height.
    * ``t`` — the shell section thickness of the FIRST region of the
      label (a differing thickness in the same label is noted).
    * ``hw`` — TOTAL stack height: max z - min z over the label's regions.
    * ``E`` — the first region's concrete modulus (kPa), feeding the
      default fc' via :func:`fc_from_E`.

    Membrane-behavior and non-vertical walls are skipped with a note
    (they have no pier forces either).
    """
    auto = getattr(model, "auto_pier_walls", False)
    out: Dict[str, dict] = {}
    tol = 1e-6
    for r in model.shells:
        if r.kind != "wall":
            continue
        label = getattr(r, "pier", "") or (r.uid if auto else "")
        if not label:
            continue
        entry = out.setdefault(label, {"t": 0.0, "hw": 0.0, "E": 0.0,
                                       "stories": {}, "notes": [],
                                       "_zlo": math.inf, "_zhi": -math.inf})
        if r.behavior != "shell":
            entry["notes"].append(f"wall {r.uid!r}: membrane behavior has "
                                  "no pier forces; skipped")
            continue
        c = [tuple(map(float, p)) for p in r.corners]
        # plane normal from the CCW corners; vertical wall check as in the
        # engine (|nz| / |n| <= 1e-3)
        ux = (c[1][0] - c[0][0], c[1][1] - c[0][1], c[1][2] - c[0][2])
        vx = (c[3][0] - c[0][0], c[3][1] - c[0][1], c[3][2] - c[0][2])
        n = (ux[1] * vx[2] - ux[2] * vx[1],
             ux[2] * vx[0] - ux[0] * vx[2],
             ux[0] * vx[1] - ux[1] * vx[0])
        n_len = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)
        if n_len < 1e-12 or abs(n[2]) / n_len > 1e-3:
            entry["notes"].append(f"wall {r.uid!r}: not vertical; skipped")
            continue
        nhat = (n[0] / n_len, n[1] / n_len, n[2] / n_len)
        # hhat = ez x nhat (in-plane horizontal axis, CONTRACT v0.15)
        hh = (-nhat[1], nhat[0], 0.0)
        hlen = math.hypot(hh[0], hh[1])
        hh = (hh[0] / hlen, hh[1] / hlen)
        svals = [p[0] * hh[0] + p[1] * hh[1] for p in c]
        extent = max(svals) - min(svals)
        zs = [p[2] for p in c]
        z_lo, z_hi = min(zs), max(zs)
        ssec = model.shell_sections.get(r.section)
        if ssec is None:
            entry["notes"].append(f"wall {r.uid!r}: unknown shell section "
                                  f"{r.section!r}; skipped")
            continue
        mat = model.materials.get(ssec.material)
        if entry["t"] == 0.0:
            entry["t"] = float(ssec.thickness)
            entry["E"] = float(mat.E) if mat is not None else 0.0
        elif abs(entry["t"] - ssec.thickness) > tol:
            entry["notes"].append(
                f"wall {r.uid!r}: thickness {ssec.thickness} differs from "
                f"the pier's {entry['t']} — first wall's thickness used")
        entry["_zlo"] = min(entry["_zlo"], z_lo)
        entry["_zhi"] = max(entry["_zhi"], z_hi)
        for s in model.stories:
            z_bot = s.elevation - s.height
            if z_lo >= s.elevation - tol or z_hi <= z_bot + tol:
                continue                        # wall does not span story
            st = entry["stories"].setdefault(s.name, {"Lw": 0.0,
                                                      "hs": s.height})
            st["Lw"] += extent
    for entry in out.values():
        if entry["_zhi"] > entry["_zlo"]:
            entry["hw"] = entry["_zhi"] - entry["_zlo"]
        del entry["_zlo"], entry["_zhi"]
    return out


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #
@dataclass
class WallPierCheck:
    """Outcome of one wall pier check at one story (SI: kN, m, kPa)."""

    pier: str
    story: str
    Lw: float = 0.0                      # m, pier length at this story
    t: float = 0.0                       # m, wall thickness
    hs: float = 0.0                      # m, story height
    hw: float = 0.0                      # m, total pier-stack height
    hw_over_lw: float = 0.0
    alpha_c: float = 0.0                 # psi-unit shear coefficient used
    fc: float = 0.0                      # kPa
    fy: float = 0.0                      # kPa
    rho_v: float = 0.0
    rho_h: float = 0.0
    P: float = 0.0                       # kN, +compression (governing combo)
    V: float = 0.0                       # kN (governing combo, signed)
    M: float = 0.0                       # kN*m (governing combo, signed)
    ratio_pmm: Optional[float] = None    # radial D/C in (M, P) space
    ratio_shear: Optional[float] = None  # |V| / phiVn
    phiVn: Optional[float] = None        # kN
    shear_capped: bool = False           # 0.66*sqrt(fc')*Acv bound governed
    capacity_point: Optional[List[float]] = None
    #   [phiMn, phiPn] where the demand ray crosses the design boundary
    pm_points: Optional[List[List[float]]] = None
    #   [[phiMn, phiPn], ...] design interaction polyline
    boundary_required: bool = False      # ACI 18.10.6.3 trigger (any combo)
    sigma_max: float = 0.0               # kPa, max extreme-fibre stress
    sigma_limit: float = 0.0             # kPa, 0.2*fc'
    sigma_combo: str = ""                # combo giving sigma_max
    status: str = "N/A"                  # "OK" | "NG" | "N/A"
    governing_combo: str = ""
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True             # ALWAYS True — screening check only

    def to_dict(self) -> dict:
        return {
            "pier": self.pier, "story": self.story,
            "Lw": self.Lw, "t": self.t, "hs": self.hs, "hw": self.hw,
            "hw_over_lw": self.hw_over_lw, "alpha_c": self.alpha_c,
            "fc": self.fc, "fy": self.fy,
            "rho_v": self.rho_v, "rho_h": self.rho_h,
            "P": self.P, "V": self.V, "M": self.M,
            "ratio_pmm": self.ratio_pmm, "ratio_shear": self.ratio_shear,
            "phiVn": self.phiVn, "shear_capped": self.shear_capped,
            "capacity_point": (list(self.capacity_point)
                               if self.capacity_point is not None else None),
            "pm_points": ([[m, p] for m, p in self.pm_points]
                          if self.pm_points is not None else None),
            "boundary_required": self.boundary_required,
            "sigma_max": self.sigma_max, "sigma_limit": self.sigma_limit,
            "sigma_combo": self.sigma_combo,
            "status": self.status, "governing_combo": self.governing_combo,
            "notes": list(self.notes), "preliminary": True,
        }


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_wall_piers(model, results, combos: Optional[List[str]] = None, *,
                     rho_v: float = 0.0025, rho_h: float = 0.0025,
                     fy: float = 420_000.0, fc_prime: Optional[float] = None,
                     phi_tc: float = 0.9, phi_shear: float = PHI_SHEAR_WALL,
                     n_strips: int = N_STRIPS) -> List[WallPierCheck]:
    """ACI 318 wall pier checks enveloped over a set of combos.

    Parameters
    ----------
    model    : the BuildingModel the results were computed from.
    results  : engine results object (with ``.to_dict()``) or the
               CONTRACT.md results dict — its ``"piers"`` block supplies
               the per-story P/V/M design forces.
    combos   : names to envelope; every name must have pier forces (an
               additive combo or a static case — the piers block carries
               both).  Default: every ADDITIVE combo of the model.
    rho_v    : uniform vertical (longitudinal) reinforcement ratio.
    rho_h    : uniform horizontal (transverse) ratio entering rho_t*fy.
    fy       : bar yield (kPa; default 420 MPa).
    fc_prime : explicit fc' (kPa); default: derived per wall from its
               concrete modulus via :func:`fc_from_E` (ACI 19.2.2.1).

    Per pier per story the reported P/V/M and ratios are the GOVERNING
    combo's (largest max(ratio_pmm, ratio_shear)), tagged
    ``governing_combo`` — the same envelope rule as the other design
    modules; ``sigma_max``/``boundary_required`` envelope over ALL combos
    (the boundary trigger is a §18.10.6.3 "under any combo" condition).

    Raises ``ValueError`` when the model/results carry no pier forces
    (label walls via ``ShellRegion.pier`` or set ``auto_pier_walls``),
    when a requested combo has none, or when a parameter is out of range.
    """
    for name, v in (("rho_v", rho_v), ("rho_h", rho_h), ("fy", fy)):
        if not (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v > 0.0):
            raise ValueError(f"{name!r} must be a finite value > 0 "
                             f"(got {v!r})")
    if rho_v >= 0.1 or rho_h >= 0.1:
        raise ValueError("rho_v/rho_h must be < 0.1 (ratio, not percent)")
    if fc_prime is not None and not (
            isinstance(fc_prime, (int, float))
            and not isinstance(fc_prime, bool)
            and math.isfinite(fc_prime) and fc_prime > 0.0):
        raise ValueError(f"'fc_prime' must be a finite value > 0 (kPa), "
                         f"got {fc_prime!r}")

    d = results.to_dict() if hasattr(results, "to_dict") else results
    if not isinstance(d, dict):
        raise TypeError("results must be a results object or a results dict")
    piers_block = d.get("piers") or {}
    if not piers_block:
        raise ValueError(
            "results carry no wall pier forces — label walls with "
            "ShellRegion.pier or set model.auto_pier_walls = True")
    if combos is None:
        combos = [name for name, cb in model.combos.items()
                  if getattr(cb, "combo_type", "add") == "add"]
        if not combos:
            raise ValueError("no additive combos to check — supply "
                             "'combos' (combo or static case names) or add "
                             "combinations to the model")
    missing = [c for c in combos if c not in piers_block]
    if missing:
        raise ValueError(
            f"no pier forces for {', '.join(repr(c) for c in missing)} "
            f"(available: {', '.join(sorted(piers_block))})")

    geometry = pier_geometry(model)
    story_order = {s.name: i for i, s in enumerate(model.stories)}
    checks: List[WallPierCheck] = []
    diagram_cache: Dict[Tuple[float, float, float], List[dict]] = {}

    for label in sorted(geometry):
        geo = geometry[label]
        fc = float(fc_prime) if fc_prime is not None else fc_from_E(geo["E"])
        for story in sorted(geo["stories"],
                            key=lambda s: story_order.get(s, 1_000_000)):
            st_geo = geo["stories"][story]
            Lw, hs = st_geo["Lw"], st_geo["hs"]
            t = geo["t"]
            chk = WallPierCheck(pier=label, story=story, Lw=Lw, t=t, hs=hs,
                                hw=geo["hw"], fc=fc, fy=fy,
                                rho_v=rho_v, rho_h=rho_h)
            chk.notes.extend(geo["notes"])
            checks.append(chk)
            if Lw <= 0.0 or t <= 0.0:
                chk.notes.append("degenerate pier geometry — not checked")
                continue

            key = (round(Lw, 9), round(t, 9), fc)
            pts = diagram_cache.get(key)
            if pts is None:
                pts = wall_interaction(Lw, t, rho_v, fc, fy,
                                       n_strips=n_strips, phi_tc=phi_tc)
                diagram_cache[key] = pts
            polyline = [(p["phiMn"], p["phiPn"]) for p in pts]
            sh = wall_shear_strength(Lw, t, geo["hw"], rho_h, fc, fy,
                                     phi_shear=phi_shear)
            chk.hw_over_lw = sh["hw_over_lw"]
            chk.alpha_c = sh["alpha_c"]
            chk.phiVn = sh["phiVn"]
            chk.shear_capped = sh["capped"]
            chk.pm_points = polyline
            chk.sigma_limit = BOUNDARY_STRESS_FRACTION * fc

            gov = None      # (ratio, combo, P, V, M, ratio_pmm, ratio_shear)
            for cname in combos:
                f = (piers_block.get(cname) or {}).get(label, {}).get(story)
                if f is None:
                    continue
                P, V, M = float(f["P"]), float(f["V"]), float(f["M"])
                r_pmm = _radial_ratio(polyline, abs(M), P)
                r_v = abs(V) / sh["phiVn"] if sh["phiVn"] > 0.0 else None
                be = boundary_element_check(P, M, Lw, t, fc)
                if be["sigma"] > chk.sigma_max or not chk.sigma_combo:
                    chk.sigma_max = be["sigma"]
                    chk.sigma_combo = cname
                if be["required"]:
                    chk.boundary_required = True
                r_all = max(r for r in (r_pmm, r_v) if r is not None) \
                    if (r_pmm is not None or r_v is not None) else None
                if r_all is not None and (gov is None or r_all > gov[0]):
                    gov = (r_all, cname, P, V, M, r_pmm, r_v)
            if gov is None:
                chk.notes.append("no pier forces for this story in the "
                                 "checked combos")
                continue
            (_, chk.governing_combo, chk.P, chk.V, chk.M,
             chk.ratio_pmm, chk.ratio_shear) = gov
            if chk.ratio_pmm is not None and chk.ratio_pmm > 1e-12:
                chk.capacity_point = [abs(chk.M) / chk.ratio_pmm,
                                      chk.P / chk.ratio_pmm]
            ratio = max(r for r in (chk.ratio_pmm, chk.ratio_shear)
                        if r is not None)
            chk.status = "OK" if ratio <= 1.0 else "NG"
            chk.notes.append("uniform-reinforcing pier check, IN-PLANE "
                             "bending only (out-of-plane out of scope in "
                             "v0.18)")
    if not checks:
        raise ValueError("results carry pier forces but the model has no "
                         "matching labeled wall geometry")
    return checks


def summarize_walls(checks: List[WallPierCheck]) -> dict:
    """Roll-up: counts, max governing ratio, governing pier/story, and the
    number of stories triggering boundary elements."""
    ok = sum(1 for c in checks if c.status == "OK")
    ng = sum(1 for c in checks if c.status == "NG")
    na = sum(1 for c in checks if c.status == "N/A")
    governing = None
    max_ratio = 0.0
    for c in checks:
        for r in (c.ratio_pmm, c.ratio_shear):
            if r is not None and r > max_ratio:
                max_ratio = r
                governing = f"{c.pier}/{c.story}"
    return {"n": len(checks), "ok": ok, "ng": ng, "na": na,
            "max_ratio": max_ratio, "governing": governing,
            "boundary_stories": sum(1 for c in checks
                                    if c.boundary_required),
            "preliminary": True}
