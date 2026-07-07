"""ASCE 41-17 automatic plastic-hinge backbones (v0.19) — PRELIMINARY.

Generates moment-rotation backbones and acceptance criteria for the
``hinges == "asce41"`` pushover mode: every member flagged
``FrameMember.hinges == "auto_m3"`` gets a trilinear M3 hinge at both ends,
sized from ASCE 41-17 modeling-parameter tables:

STEEL members (the section name resolves in the design-property library,
:func:`skyframe.design.steel.design_properties`) — Table 9-7.1, structural
steel BEAMS, flexure rows::

    ladder condition                              a       b      c   IO    LS   CP
    bf/2tf <= 0.30 sqrt(E/Fy)  AND
    h/tw   <= 2.45 sqrt(E/Fy)   (compact)        9thy   11thy  0.6  1thy  9thy 11thy
    bf/2tf >= 0.38 sqrt(E/Fy)  OR
    h/tw   >= 3.76 sqrt(E/Fy)   (slender)        4thy    6thy  0.2  .25thy 3thy 4thy

with LINEAR interpolation between the two ladders; when both the flange
and the web interpolate, the WORSE (larger) interpolation factor governs
(the table's "linear interpolation ... the lower resulting value shall be
used" rule).  ``h/tw`` uses the clear web depth ``d - 2 tf`` (the fillet
``k`` dimension is not in the library — slightly conservative, documented).

    thy = Z Fye L / (6 E I)                       (ASCE 41-17 Eq. 9-1, beams)
    My  = Z Fye,   Fye = expected_factor * Fy     (Ry = 1.1 default, A992)

COLUMNS in v0.19 use the SAME beam-flexure ladder with NO axial
interaction (P/Pye = 0 in Eq. 9-2, the a/b/c axial reductions of the
column rows are not applied) — a documented preliminary idealization;
flag ``preliminary`` is set on every backbone.

CONCRETE members (rectangular drawing dims b/h > 0, not in the steel
library) — Table 10-7, RC BEAMS controlled by flexure, CONFORMING
transverse reinforcement, low shear (V <= 0.25 sqrt(fc') MPa-units)::

    (rho - rho') / rho_bal      a       b      c     IO     LS     CP
    <= 0.0                    0.025   0.05   0.2   0.010  0.025  0.05
    >= 0.5                    0.020   0.04   0.2   0.005  0.020  0.04

(absolute plastic rotations, rad; linear interpolation between).  ``My``
is the nominal Mn from :func:`skyframe.design.concrete.beam_flexure` with
``As = rho * b * d_eff`` and ``d_eff = 0.9 h`` (documented estimate);
``thy = My L / (6 E I)`` (the Eq. 9-1 form with M/EI in place of ZFye/EI).

The backbone dict feeds the engine's Hysteretic hinge spring (see
CONTRACT v0.19): the spring (elastic stiffness ``k = n 6EI/L``, n = 10,
the v0.5 stiff-hinge idealization) yields at ``(My, My/k)``, hardens at
3% of the member-end stiffness ``6EI/L`` (ASCE 41 §9.4.2.2 permitted
strain hardening) to ``(Mc, My/k + a)``, then degrades to the residual
``(c My, My/k + b)`` and stays flat.

All values SI: kN, m, kPa, rad.
"""

from __future__ import annotations

import math
from typing import Optional

from skyframe.core.model import BuildingModel, FrameMember
from skyframe.design.steel import design_properties
from skyframe.design.concrete import beam_flexure

# stiff-hinge series factor (matches the v0.5 pushover idealization)
HINGE_N = 10.0
# post-yield hardening as a fraction of the member-end stiffness 6EI/L
HARDENING = 0.03
# expected-strength factor Fye = RY_DEFAULT * Fy (ASCE 41-17 Table 9-3, A992)
RY_DEFAULT = 1.1

# Table 9-7.1 flexure ladders (multiples of theta_y) --------------------------
_STEEL_COMPACT = {"a": 9.0, "b": 11.0, "c": 0.6, "IO": 1.0, "LS": 9.0,
                  "CP": 11.0}
_STEEL_SLENDER = {"a": 4.0, "b": 6.0, "c": 0.2, "IO": 0.25, "LS": 3.0,
                  "CP": 4.0}
# compactness ladder limits (x sqrt(E/Fy))
_FLANGE_LO, _FLANGE_HI = 0.30, 0.38
_WEB_LO, _WEB_HI = 2.45, 3.76

# Table 10-7 rows (absolute plastic rotation, rad) ----------------------------
_CONC_LO = {"a": 0.025, "b": 0.05, "c": 0.2, "IO": 0.010, "LS": 0.025,
            "CP": 0.05}
_CONC_HI = {"a": 0.020, "b": 0.04, "c": 0.2, "IO": 0.005, "LS": 0.020,
            "CP": 0.04}


def _interp(lo: dict, hi: dict, t: float) -> dict:
    """Linear interpolation between two table rows at 0 <= t <= 1."""
    t = min(max(t, 0.0), 1.0)
    return {k: lo[k] + t * (hi[k] - lo[k]) for k in lo}


def steel_hinge_backbone(props: dict, Fy: float, E: float, I: float,
                         L: float, *,
                         expected_factor: float = RY_DEFAULT) -> dict:
    """ASCE 41-17 Table 9-7.1 steel M3 backbone (see module docstring).

    ``props`` is a :func:`design_properties` row (d, bf, tw, tf, Zx in m);
    ``Fy`` kPa, ``E`` kPa, ``I`` m^4 (strong axis), ``L`` m member length.
    Returns the backbone dict (My kN*m; thy/a/b/IO/LS/CP rad; c ratio).
    """
    if Fy <= 0.0 or E <= 0.0 or I <= 0.0 or L <= 0.0:
        raise ValueError("steel hinge: Fy, E, I, L must all be > 0")
    root = math.sqrt(E / Fy)
    lam_f = (props["bf"] / (2.0 * props["tf"])) / root
    lam_w = ((props["d"] - 2.0 * props["tf"]) / props["tw"]) / root
    # interpolation factor per ladder: 0 at the compact limit, 1 at slender
    t_f = (lam_f - _FLANGE_LO) / (_FLANGE_HI - _FLANGE_LO)
    t_w = (lam_w - _WEB_LO) / (_WEB_HI - _WEB_LO)
    t = min(max(max(t_f, t_w), 0.0), 1.0)   # the worse slenderness governs
    row = _interp(_STEEL_COMPACT, _STEEL_SLENDER, t)
    Fye = expected_factor * Fy
    My = props["Zx"] * Fye                  # kN*m (kPa * m^3)
    thy = props["Zx"] * Fye * L / (6.0 * E * I)     # Eq. 9-1
    return {"kind": "steel", "My": My, "thy": thy,
            "a": row["a"] * thy, "b": row["b"] * thy, "c": row["c"],
            "IO": row["IO"] * thy, "LS": row["LS"] * thy,
            "CP": row["CP"] * thy, "slenderness_t": t,
            "preliminary": True}


def concrete_hinge_backbone(b: float, h: float, fc: float, E: float,
                            I: float, L: float, *, rho: float = 0.01,
                            rho_prime: float = 0.0,
                            fy_bar: float = 420000.0) -> dict:
    """ASCE 41-17 Table 10-7 concrete-beam M3 backbone (module docstring).

    ``b``/``h`` section drawing dims (m), ``fc`` concrete fc' (kPa),
    ``E`` kPa, ``I`` m^4, ``L`` m; ``rho``/``rho_prime`` tension /
    compression steel ratios of ``b * d_eff``; ``fy_bar`` kPa.
    """
    if min(b, h, fc, E, I, L) <= 0.0:
        raise ValueError("concrete hinge: b, h, fc, E, I, L must be > 0")
    if not 0.0 < rho < 0.1:
        raise ValueError(f"concrete hinge: rho must be in (0, 0.1), "
                         f"got {rho!r}")
    d_eff = 0.9 * h                                       # documented estimate
    As = rho * b * d_eff
    Mn = beam_flexure(b, d_eff, As, fc, fy_bar)["Mn"]     # kN*m
    # balanced ratio (ACI, eps_cu = 0.003, Es = 200 GPa):
    #   rho_bal = 0.85 beta1 fc/fy * 600/(600 + fy[MPa])
    from skyframe.design.concrete import beta1 as _beta1
    fy_mpa = fy_bar / 1000.0
    rho_bal = 0.85 * _beta1(fc) * (fc / fy_bar) * 600.0 / (600.0 + fy_mpa)
    t = ((rho - rho_prime) / rho_bal) / 0.5               # 0 at 0.0, 1 at 0.5
    row = _interp(_CONC_LO, _CONC_HI, t)
    thy = Mn * L / (6.0 * E * I)
    return {"kind": "concrete", "My": Mn, "thy": thy,
            "a": row["a"], "b": row["b"], "c": row["c"],
            "IO": row["IO"], "LS": row["LS"], "CP": row["CP"],
            "rho_ratio": (rho - rho_prime) / rho_bal, "preliminary": True}


def auto_backbone(model: BuildingModel, m: FrameMember, *,
                  expected_factor: float = RY_DEFAULT, rho: float = 0.01,
                  rho_prime: float = 0.0,
                  fy_bar: float = 420000.0) -> Optional[dict]:
    """Backbone for one ``hinges == "auto_m3"`` member, or None + reason.

    Steel path when the member's section name resolves in the design
    library (Fy from the material's ``fy`` if set, else 345 MPa A992
    default, documented); otherwise the concrete path needs drawing dims
    b/h > 0.  Returns ``None`` when neither applies (the engine warns and
    leaves the member elastic).
    """
    sec = model.sections[m.section]
    mat = model.materials[sec.material]
    E = mat.E
    I = sec.I33 * sec.mod_I33
    L = m.length
    props = design_properties(sec.name)
    if props is not None:
        Fy = getattr(mat, "fy", 0.0) or 345000.0          # kPa (A992)
        return steel_hinge_backbone(props, Fy, E, I, L,
                                    expected_factor=expected_factor)
    if sec.b > 0.0 and sec.h > 0.0:
        from skyframe.design.wall import fc_from_E
        fc = getattr(mat, "fc", 0.0) or fc_from_E(E)
        # fc from E via inverted ACI 19.2.2.1 (E = 4700 sqrt(fc') MPa)
        # when the material carries no explicit fc — design.wall.fc_from_E.
        # (v0.21 fix: the old inline formula skipped the kPa -> MPa
        # conversion, inflating the implied fc' by 1e6; the path is only
        # reached for materials carrying no explicit ``fc`` attribute.)
        return concrete_hinge_backbone(sec.b, sec.h, fc, E, I, L, rho=rho,
                                       rho_prime=rho_prime, fy_bar=fy_bar)
    return None


def hinge_state(rotation: float, backbone: dict, k_spring: float) -> str:
    """Acceptance state of a hinge at a TOTAL spring rotation (rad).

    The spring rotation includes the elastic part ``My / k_spring``;
    acceptance criteria are PLASTIC rotations, so the bands are shifted
    by the spring yield rotation.  Returns "elastic" | "IO" | "LS" |
    "CP" | "collapse" ("IO" = yielded but within the IO limit, etc.;
    "collapse" = beyond CP).
    """
    ty = backbone["My"] / k_spring
    p = abs(rotation) - ty
    if p <= 1e-12:
        return "elastic"
    if p <= backbone["IO"]:
        return "IO"
    if p <= backbone["LS"]:
        return "LS"
    if p <= backbone["CP"]:
        return "CP"
    return "collapse"
