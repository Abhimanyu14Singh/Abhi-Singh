"""PRELIMINARY Eurocode 2 (EN 1992-1-1:2004) checks for rectangular members
(v0.23).

Screens SkyFrame rectangular concrete frame members against simplified EN
1992-1-1 provisions, over the SAME demand pipeline (and the same
:class:`~skyframe.design.concrete.RebarLayout` /
:class:`~skyframe.design.concrete.ConcreteCheck` shapes) as the ACI module:

* Beams, flexure (§6.1) — singly-reinforced rectangular stress block for
  fck <= 50 MPa (``lambda = 0.8``, ``eta = 1.0`` — §3.1.7(3))::

      fcd = fck/gamma_C (1.5);   fyd = fyk/gamma_S (1.15)
      x   = As*fyd / (0.8*b*fcd)          (neutral-axis depth)
      MRd = As*fyd * (d - 0.4*x)

  checked separately for SAGGING (bottom steel vs max positive station
  M3) and HOGGING (top steel).  Ductility: a note when ``x/d > 0.45``
  (the classic redistribution limit for <= C50 — EC2 has no phi-style
  transition; the material factors already sit in fcd/fyd).  Minimum
  steel per §9.2.1.1: ``As,min = max(0.26*fctm/fyk, 0.0013)*b*d`` with
  ``fctm = 0.30*fck[MPa]^(2/3)`` (Table 3.1, <= C50) — a violation flags
  the member NG (same policy as the ACI rho_min check).
* Beams, shear (§6.2) — members WITH shear reinforcement resist
  ``VRd = min(VRd,s, VRd,max)`` (EC2 does NOT add a concrete term to
  VRd,s — a real difference from ACI's Vc + Vs, documented)::

      VRd,s   = (Asw/s)*z*fywd*cot(theta),  z = 0.9d, cot(theta) = 2.5
      VRd,max = b*z*nu1*fcd/(cot + tan),    nu1 = 0.6*(1 - fck/250 MPa)

  and the no-stirrup concrete resistance §6.2.2 is always reported::

      VRd,c = max(CRd,c*k*(100*rho_l*fck)^(1/3), vmin) * b*d
      CRd,c = 0.18/gamma_C = 0.12;  k = 1 + sqrt(200/d[mm]) <= 2.0
      rho_l = Asl/(b*d) <= 0.02;    vmin = 0.035*k^1.5*sqrt(fck)

  (stress terms in MPa, converted explicitly).  The governing shear
  capacity is ``max(VRd,c, min(VRd,s, VRd,max))`` — a section whose
  stirrups are weaker than plain concrete keeps VRd,c (§6.2.1(4)).
* Columns (§6.1) — uniaxial N-M interaction about the LOCAL-3 axis via
  the SAME two-face strain-compatibility machinery as
  :mod:`skyframe.design.concrete` (documented differences from the ACI
  path: NO strength-reduction phi — the material factors gamma_C/gamma_S
  already live in fcd/fyd; stress block ``lambda = 0.8, eta = 1.0``
  instead of 0.85fc'/beta1; crushing strain ``eps_cu2 = 0.0035`` instead
  of 0.003; NO 0.80 accidental-eccentricity cap on pure compression —
  EC2 handles that through minimum-eccentricity/imperfection rules that
  are out of scope here).  Demand rated by the same radial scaling.
  Biaxial demand is NOT checked (§5.8.9 deferred — a note is emitted
  when |M2| exceeds 5% of |M3|'s scale, mirroring the ACI trigger).

THESE ARE PRELIMINARY SCREENING CHECKS, NOT A FINAL CODE CHECK.  Not
covered: slenderness/second-order (§5.8), biaxial columns, torsion,
detailing, anchorage, crack control, deflections, fck > 50 MPa (the
lambda/eta and fctm laws used are the <= C50 forms — enforced with a
ValueError).  Every result carries ``"preliminary": True``.

Units: SkyFrame SI — kN, m, kPa (fck = 30 MPa passed as 30_000 kPa; the
``RebarLayout.fy`` field is read as the CHARACTERISTIC fyk here).  All
kPa -> MPa conversions are written explicitly at the point of use.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from skyframe.design.concrete import (ES_REBAR, ConcreteCheck, RebarLayout,
                                      _beam_demands, _case_block,
                                      _column_demands, _radial_ratio,
                                      _validate_layout)

__all__ = [
    "check_concrete_members_ec2", "check_concrete_members_ec2_envelope",
    "summarize_ec2", "beam_flexure_ec2", "shear_vrdc", "shear_vrds",
    "column_interaction_ec2", "GAMMA_C", "GAMMA_S", "EPS_CU2",
    "LAMBDA_BLOCK", "ETA_BLOCK", "COT_THETA",
]

GAMMA_C = 1.5          # concrete partial factor (Table 2.1N, persistent)
GAMMA_S = 1.15         # reinforcement partial factor
EPS_CU2 = 0.0035       # ultimate concrete strain, fck <= 50 MPa (Table 3.1)
LAMBDA_BLOCK = 0.8     # rectangular stress-block depth factor (§3.1.7(3))
ETA_BLOCK = 1.0        # rectangular stress-block strength factor (<= C50)
COT_THETA = 2.5        # strut angle for VRd,s (the §6.2.3(2) upper limit)
FCK_MAX = 50_000.0     # kPa — the <= C50 laws implemented here


def _check_fck(fck: float) -> None:
    if not (isinstance(fck, (int, float)) and not isinstance(fck, bool)
            and math.isfinite(fck) and 0.0 < fck <= FCK_MAX):
        raise ValueError(f"fck must be a finite value in (0, {FCK_MAX}] kPa "
                         f"(<= C50 laws only), got {fck!r}")


# --------------------------------------------------------------------------- #
# capacity building blocks (module-level so tests can exercise them directly)
# --------------------------------------------------------------------------- #
def beam_flexure_ec2(b: float, d: float, As: float, fck: float,
                     fyk: float) -> dict:
    """EC2 singly-reinforced rectangular flexural strength (one face).

    fcd = fck/1.5, fyd = fyk/1.15 (design strengths);
    x   = As*fyd / (lambda*b*eta*fcd)   with lambda = 0.8, eta = 1.0;
    MRd = As*fyd * (d - lambda*x/2) = As*fyd*(d - 0.4*x)   [kN*m].

    Returns x, x_over_d, fcd, fyd, MRd, As_min (§9.2.1.1 with
    fctm = 0.30*fck^(2/3) MPa), steel_yields (eps_s at x vs fyd/Es) and
    ductile (x/d <= 0.45).
    """
    _check_fck(fck)
    if min(b, d, As, fyk) <= 0.0:
        raise ValueError("b, d, As, fyk must be > 0")
    fcd = fck / GAMMA_C                          # kPa
    fyd = fyk / GAMMA_S                          # kPa
    x = As * fyd / (LAMBDA_BLOCK * b * ETA_BLOCK * fcd)   # m
    MRd = As * fyd * (d - LAMBDA_BLOCK * x / 2.0)         # kN*m
    fck_MPa = fck / 1000.0                       # kPa -> MPa (explicit)
    fctm = 0.30 * fck_MPa ** (2.0 / 3.0)         # MPa (Table 3.1, <= C50)
    fyk_MPa = fyk / 1000.0                       # kPa -> MPa
    As_min = max(0.26 * fctm / fyk_MPa, 0.0013) * b * d   # m^2
    eps_s = EPS_CU2 * (d - x) / x if x > 0.0 else math.inf
    return {"x": x, "x_over_d": x / d, "fcd": fcd, "fyd": fyd, "MRd": MRd,
            "As_min": As_min, "steel_yields": eps_s >= fyd / ES_REBAR,
            "ductile": x / d <= 0.45}


def shear_vrdc(b: float, d: float, Asl: float, fck: float) -> dict:
    """EC2 §6.2.2 no-shear-reinforcement resistance VRd,c [kN].

    VRd,c = max(CRd,c*k*(100*rho_l*fck)^(1/3), vmin) * b*d with the
    stress term in MPa (fck in MPa inside the cube root), CRd,c =
    0.18/gamma_C = 0.12, k = 1 + sqrt(200/d[mm]) <= 2.0, rho_l =
    Asl/(b*d) <= 0.02, vmin = 0.035*k^1.5*sqrt(fck[MPa]).  The MPa
    stress converts to kPa (x1000) so stress*area = kN.
    """
    _check_fck(fck)
    if min(b, d) <= 0.0 or Asl < 0.0:
        raise ValueError("b, d must be > 0 and Asl >= 0")
    fck_MPa = fck / 1000.0                        # kPa -> MPa
    d_mm = d * 1000.0                             # m -> mm (explicit)
    k = min(1.0 + math.sqrt(200.0 / d_mm), 2.0)
    rho_l = min(Asl / (b * d), 0.02)
    CRdc = 0.18 / GAMMA_C                         # = 0.12
    v = CRdc * k * (100.0 * rho_l * fck_MPa) ** (1.0 / 3.0)   # MPa
    vmin = 0.035 * k ** 1.5 * math.sqrt(fck_MPa)              # MPa
    governed_by_vmin = vmin > v
    v_gov = max(v, vmin)
    return {"k": k, "rho_l": rho_l, "v": v, "vmin": vmin,
            "governed_by_vmin": governed_by_vmin,
            "VRdc": v_gov * 1000.0 * b * d}       # MPa -> kPa, * m^2 = kN


def shear_vrds(b: float, d: float, Asw: float, s: float, fck: float,
               fywk: float, *, cot_theta: float = COT_THETA) -> dict:
    """EC2 §6.2.3 shear resistance WITH stirrups [kN].

    VRd,s   = (Asw/s)*z*fywd*cot(theta), z = 0.9d, fywd = fywk/1.15;
    VRd,max = b*z*nu1*fcd/(cot(theta) + tan(theta)),
              nu1 = 0.6*(1 - fck[MPa]/250)   (Eq. 6.9 / 6.6N).
    Returns VRds, VRdmax, VRd = min of the two, crushing (bool).
    """
    _check_fck(fck)
    if min(b, d, Asw, s, fywk) <= 0.0 or cot_theta <= 0.0:
        raise ValueError("b, d, Asw, s, fywk, cot_theta must be > 0")
    z = 0.9 * d                                   # m
    fywd = fywk / GAMMA_S                         # kPa
    VRds = (Asw / s) * z * fywd * cot_theta       # kN
    fck_MPa = fck / 1000.0                        # kPa -> MPa
    nu1 = 0.6 * (1.0 - fck_MPa / 250.0)
    fcd = fck / GAMMA_C                           # kPa
    VRdmax = b * z * nu1 * fcd / (cot_theta + 1.0 / cot_theta)   # kN
    crushing = VRds > VRdmax
    return {"z": z, "fywd": fywd, "VRds": VRds, "nu1": nu1,
            "VRdmax": VRdmax, "VRd": min(VRds, VRdmax),
            "crushing": crushing}


def _column_section_forces_ec2(c: float, b: float, h: float, d: float,
                               dp: float, As_t: float, As_c: float,
                               fcd: float, fyd: float
                               ) -> Tuple[float, float, float]:
    """(NRd, MRd, eps_t) at neutral-axis depth c — EC2 strain compatibility.

    The EXACT structure of :func:`skyframe.design.concrete.
    _column_section_forces` with the EC2 block: uniform ``eta*fcd`` over
    ``a = min(lambda*c, h)`` (lambda = 0.8, eta = 1.0), crushing strain
    ``eps_cu2 = 0.0035``, steel stresses clamped to +-fyd.  Displaced
    concrete at the compression steel is ignored (same documented
    approximation as the ACI path).  NRd +compression (kN), MRd about
    mid-height (kN*m).
    """
    a = min(LAMBDA_BLOCK * c, h)
    Cc = ETA_BLOCK * fcd * a * b                        # kN
    eps_c = EPS_CU2 * (c - dp) / c                      # comp.-steel strain
    fs_c = max(-fyd, min(fyd, ES_REBAR * eps_c))        # kPa, +compression
    eps_t = EPS_CU2 * (d - c) / c                       # tension-steel strain
    fs_t = max(-fyd, min(fyd, ES_REBAR * eps_t))        # kPa, +tension
    Pn = Cc + As_c * fs_c - As_t * fs_t                 # kN
    Mn = Cc * (h / 2.0 - a / 2.0) + As_c * fs_c * (h / 2.0 - dp) \
        + As_t * fs_t * (d - h / 2.0)                   # kN*m
    return Pn, Mn, eps_t


def column_interaction_ec2(b: float, h: float, d: float, dp: float,
                           As_face: float, fck: float, fyk: float
                           ) -> List[dict]:
    """5-point uniaxial N-M design interaction of a symmetric EC2 column.

    Same point structure as the ACI diagram, DESIGN values directly (no
    phi — gamma_C/gamma_S already inside fcd/fyd; no 0.80 compression
    cap, see module docstring):

    1. ``"pure compression"`` — NRd0 = eta*fcd*(Ag - Ast) + fyd*Ast
       (steel at fyd — exact when fyd <= Es*eps_c2 = 400 MPa, true for
       every fyk <= 460 MPa; documented), MRd = 0.
    2. ``"eps_t = 0"``  — strain compatibility at c = d.
    3. ``"balanced"``   — c_b = d*eps_cu2/(eps_cu2 + fyd/Es).
    4. ``"pure bending"`` — c bisected for NRd = 0.
    5. ``"pure tension"`` — NRd = -fyd*Ast, MRd = 0.

    Each point: {label, c, NRd, MRd, eps_t}.
    """
    _check_fck(fck)
    if min(b, h, As_face, fyk) <= 0.0 or not (0.0 < dp < d <= h):
        raise ValueError("need b, h, As_face, fyk > 0 and 0 < dp < d <= h")
    fcd = fck / GAMMA_C
    fyd = fyk / GAMMA_S
    Ag = b * h
    Ast = 2.0 * As_face
    eps_yd = fyd / ES_REBAR
    pts: List[dict] = []

    NRd0 = ETA_BLOCK * fcd * (Ag - Ast) + fyd * Ast
    pts.append({"label": "pure compression", "c": math.inf, "NRd": NRd0,
                "MRd": 0.0, "eps_t": -EPS_CU2})

    for label, c in (("eps_t = 0", d),
                     ("balanced", d * EPS_CU2 / (EPS_CU2 + eps_yd))):
        Pn, Mn, eps_t = _column_section_forces_ec2(c, b, h, d, dp, As_face,
                                                   As_face, fcd, fyd)
        pts.append({"label": label, "c": c, "NRd": Pn, "MRd": Mn,
                    "eps_t": eps_t})

    # pure bending: bisect c in (0, d] for NRd = 0 (monotonic in c)
    lo, hi = 1e-6 * h, d
    Pn_lo = _column_section_forces_ec2(lo, b, h, d, dp, As_face, As_face,
                                       fcd, fyd)[0]
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        Pn_mid = _column_section_forces_ec2(mid, b, h, d, dp, As_face,
                                            As_face, fcd, fyd)[0]
        if (Pn_lo < 0.0) == (Pn_mid < 0.0):
            lo, Pn_lo = mid, Pn_mid
        else:
            hi = mid
    c0 = 0.5 * (lo + hi)
    Pn, Mn, eps_t = _column_section_forces_ec2(c0, b, h, d, dp, As_face,
                                               As_face, fcd, fyd)
    pts.append({"label": "pure bending", "c": c0, "NRd": Pn, "MRd": Mn,
                "eps_t": eps_t})

    pts.append({"label": "pure tension", "c": 0.0, "NRd": -fyd * Ast,
                "MRd": 0.0, "eps_t": math.inf})
    return pts


# --------------------------------------------------------------------------- #
# per-member checks
# --------------------------------------------------------------------------- #
def _check_beam_ec2(chk: ConcreteCheck, case: dict, b: float, h: float,
                    lay: RebarLayout, fck: float) -> None:
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
    fcd = fck / GAMMA_C
    if abs(chk.Pu) > 0.10 * fcd * b * h:
        chk.notes.append("|NEd| exceeds 0.10*fcd*Ag — beam flexure check "
                         "ignores axial force (preliminary)")

    ratios: List[Tuple[float, str]] = []
    ok = True
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
        fx = beam_flexure_ec2(b, d, As, fck, lay.fy)
        setattr(chk, attr, fx["MRd"])           # design MRd (no phi in EC2)
        if not fx["ductile"]:
            chk.notes.append(f"{face}: x/d = {fx['x_over_d']:.3f} > 0.45 "
                             "(low ductility for <= C50 — EC2 5.5 "
                             "redistribution limit)")
        if not fx["steel_yields"]:
            chk.notes.append(f"{face}: tension steel below fyd at ULS — "
                             "the stress-block MRd is approximate")
        if As < fx["As_min"]:
            chk.notes.append(f"{face}: As = {As:.6f} m^2 < As,min = "
                             f"{fx['As_min']:.6f} m^2 (EC2 9.2.1.1)")
            ok = False
        ratios.append((Mu / fx["MRd"], face))

    vc = shear_vrdc(b, d, max(lay.n_top, lay.n_bot) * lay.bar_area(), fck)
    vs = shear_vrds(b, d, lay.Av(), lay.stirrup_spacing, fck, lay.fy)
    VRd = max(vc["VRdc"], vs["VRd"])            # §6.2.1(4): the better of
    chk.phiVn = VRd                             # the two mechanisms
    if vs["crushing"]:
        chk.notes.append("VRd,s capped by strut crushing VRd,max "
                         "(EC2 6.2.3, cot theta = 2.5)")
    if vc["VRdc"] > vs["VRd"]:
        chk.notes.append("VRd,c (no-stirrup) governs over VRd,s")
    ratios.append((chk.Vu / VRd, "shear"))

    chk.ratio, chk.equation = max(ratios)
    chk.status = "OK" if (chk.ratio <= 1.0 and ok) else "NG"


def _check_column_ec2(chk: ConcreteCheck, case: dict, b: float, h: float,
                      lay: RebarLayout, fck: float) -> None:
    if lay.n_top != lay.n_bot:
        chk.notes.append(f"unsymmetric layout (n_top = {lay.n_top} != "
                         f"n_bot = {lay.n_bot}) not supported for the EC2 "
                         "column interaction — not checked")
        return
    dem = _column_demands(case, chk.uid)
    if dem is None:
        chk.notes.append("no member forces in results for this member")
        return
    chk.Pu, chk.Mu, chk.Mu22 = dem
    d = lay.d_eff(h)
    dp = lay.cover + lay.stirrup_dia + lay.bar_dia / 2.0
    if d <= dp:
        chk.notes.append(f"effective depth d = {d:.3f} m <= d' = {dp:.3f} m"
                         " (cover/stirrup/bar too large for h)")
        return
    As_face = lay.n_top * lay.bar_area()
    pts = column_interaction_ec2(b, h, d, dp, As_face, fck, lay.fy)
    chk.pm_points = [(p["MRd"], p["NRd"]) for p in pts]
    ratio = _radial_ratio(chk.pm_points, chk.Mu, chk.Pu)
    if ratio is None:                          # defensive; should not happen
        chk.notes.append("interaction ratio could not be computed")
        return
    m_ref = max(m for m, _ in chk.pm_points)
    if chk.Mu22 > 0.05 * m_ref:
        chk.notes.append("biaxial demand present — EC2 §5.8.9 biaxial "
                         "check not implemented (uniaxial local-3 only)")
    chk.notes.append("EC2 uniaxial interaction: design values (no phi — "
                     "gamma_C/gamma_S in fcd/fyd), lambda = 0.8, eta = 1, "
                     "eps_cu2 = 0.0035, no 0.80 compression cap")
    chk.ratio, chk.equation = ratio, "P-M"
    chk.status = "OK" if ratio <= 1.0 else "NG"


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_concrete_members_ec2(model, results, case_or_combo: str,
                               rebar: Dict[str, RebarLayout], *,
                               fck: float = 30_000.0
                               ) -> List[ConcreteCheck]:
    """Preliminary EN 1992-1-1 checks for rectangular concrete members.

    Same call shape and result objects (:class:`ConcreteCheck`) as
    :func:`skyframe.design.concrete.check_concrete_members`; ``fck`` is
    the CHARACTERISTIC cylinder strength [kPa] (<= C50) and the
    ``RebarLayout.fy`` field is read as fyk.  The ``phiMn_pos`` /
    ``phiMn_neg`` / ``phiVn`` result fields carry the EC2 DESIGN
    resistances MRd/VRd (material factors, no phi — field names kept for
    response-shape compatibility, documented).
    """
    _check_fck(fck)
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
            _check_beam_ec2(chk, case, b, h, lay, fck)
        elif m.kind == "column":
            _check_column_ec2(chk, case, b, h, lay, fck)
        else:
            chk.notes.append(f"kind {m.kind!r} not checked (EC2 scope is "
                             "beams and columns)")
    return checks


def check_concrete_members_ec2_envelope(
        model, results, rebar: Dict[str, RebarLayout],
        combos: Optional[List[str]] = None, **kw) -> List[ConcreteCheck]:
    """Governing EC2 concrete checks over a set of load combinations.

    Same envelope rule as the ACI module: per member the LARGEST
    demand/capacity ratio wins, tagged ``governing_combo``; combos
    default to every name in ``results['combos']``.  ``**kw`` = fck.
    """
    d = results.to_dict() if hasattr(results, "to_dict") else results
    if combos is None:
        combos = list((d.get("combos") or {}).keys())
    if not combos:
        raise ValueError("check_concrete_members_ec2_envelope: no combos to "
                         "envelope (none supplied and results has no "
                         "'combos')")
    per_combo = {c: check_concrete_members_ec2(model, d, c, rebar, **kw)
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


def summarize_ec2(checks: List[ConcreteCheck]) -> dict:
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
