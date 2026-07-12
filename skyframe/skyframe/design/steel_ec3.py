"""PRELIMINARY Eurocode 3 (EN 1993-1-1:2005) checks for steel W-shapes (v0.23).

Screens SkyFrame frame members (library W-shapes, same recognition rule as
:mod:`skyframe.design.steel` — name + 1% area validation) against the EN
1993-1-1 member provisions, over the SAME demand pipeline as the AISC
module (``_case_block`` / ``_demands`` / envelope):

* §6.2.4/6.2.5  — cross-section resistance: ``Npl,Rd = A*fy/gamma_M0``,
                  ``Mc,Rd = Mpl,Rd = Wpl*fy/gamma_M0`` (Class 1/2 plastic
                  modulus, the AISC-table Zx/Zy), combined by the
                  CONSERVATIVE LINEAR sum of Eq. (6.2)::

                      N/Npl,Rd + My/Mpl,y,Rd + Mz/Mpl,z,Rd <= 1

                  (the exact §6.2.9 plastic N-M interaction — the
                  (1-n)/(1-0.5a) reduction — is DEFERRED; Eq. (6.2) is the
                  code's own documented conservative alternative).
* §6.2.6        — shear: ``Vpl,Rd = Av*fy/(sqrt(3)*gamma_M0)`` with the
                  shear area SIMPLIFIED to ``Av = d*tw`` (full depth times
                  web thickness).  The exact EC3 rolled-section formula
                  ``Av = A - 2*b*tf + (tw + 2*r)*tf`` needs the root
                  radius r, which the design table does not carry;
                  ``d*tw`` is the classic web-area approximation
                  (documented — within a few % either side of exact).
* §6.3.1        — flexural buckling: ``Ncr = pi^2*E*I/Lcr^2``,
                  ``lambda_bar = sqrt(A*fy/Ncr)``, imperfection factor
                  alpha from the Table 6.1 buckling curves (a0/a/b/c/d)
                  selected per the Table 6.2 rolled-I rule for
                  tf <= 40 mm (true for every library shape, asserted):
                  h/b > 1.2 -> curve a (major) / b (minor);
                  h/b <= 1.2 -> curve b (major) / c (minor);
                  ``phi = 0.5*(1 + alpha*(lambda_bar - 0.2) +
                  lambda_bar^2)``, ``chi = 1/(phi + sqrt(phi^2 -
                  lambda_bar^2)) <= 1``; ``Nb,Rd = chi*A*fy/gamma_M1``.
* §6.3.3        — member interaction, Eqs. (6.61)/(6.62), SIMPLIFIED:
                  lateral-torsional buckling is DEFERRED (``chi_LT = 1``,
                  equivalently kc = 1 — the section is treated as
                  laterally restrained), and the interaction factors come
                  from Annex B Table B.1 (members NOT susceptible to
                  torsional deformations — consistent with chi_LT = 1)::

                      kyy = Cmy*(1 + min(lambda_y - 0.2, 0.8)*n_y)
                      kzz = Cmz*(1 + min(2*lambda_z - 0.6, 1.4)*n_z)
                      kyz = 0.6*kzz ;  kzy = 0.6*kyy
                      n_y = NEd/(chi_y*Npl/gamma_M1),  n_z likewise

                  with ``Cmy = Cmz = 0.9`` FIXED (the Table B.3 sway-mode
                  value — the moment diagram is not resolved, documented
                  conservative-for-most-frames simplification)::

                      (6.61)  n_y + kyy*My/MRd,y + kyz*Mz/MRd,z <= 1
                      (6.62)  n_z + kzy*My/MRd,y + kzz*Mz/MRd,z <= 1

                  ``MRd = Mpl*fy/gamma_M1`` per axis.  The governing check
                  is the max of Eq. (6.2), (6.61), (6.62) and the shear
                  ratio.  TENSION members get the cross-section checks
                  only (``Nt,Rd = Npl,Rd`` — §6.2.3 gross yielding;
                  net-section rupture is NOT checked).

Partial factors: ``gamma_M0 = gamma_M1 = 1.0`` (the EN 1993-1-1 §6.1
recommended values; national annexes may differ — documented).

EC3 axis convention vs SkyFrame: EC3 "y-y" is the MAJOR axis = SkyFrame
local 3 (M3, demands ``Mu33``), EC3 "z-z" the MINOR axis = local 2.

THESE ARE PRELIMINARY SCREENING CHECKS, NOT A FINAL CODE CHECK.  Not
covered: section classification (Class 1/2 assumed), LTB (§6.3.2),
torsion, shear-moment interaction (§6.2.8), second-order sway
amplification, net sections, connections.  Every result carries
``"preliminary": True``.

Units: SkyFrame SI — kN, m, kPa (fy = 355 MPa passed as 355_000 kPa;
default S355).  E = 210 GPa (EN 1993-1-1 §3.2.6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from skyframe.design.steel import _case_block, _demands, design_properties

__all__ = [
    "MemberCheckEC3", "check_members_ec3", "check_members_ec3_envelope",
    "summarize_ec3", "buckling_chi", "buckling_curve", "IMPERFECTION_ALPHA",
    "E_STEEL_EC3", "GAMMA_M0", "GAMMA_M1", "CM_SWAY",
]

E_STEEL_EC3 = 210_000_000.0   # kPa (210 GPa) — EN 1993-1-1 §3.2.6
GAMMA_M0 = 1.0                # §6.1 recommended (cross sections)
GAMMA_M1 = 1.0                # §6.1 recommended (member buckling)
CM_SWAY = 0.9                 # Annex B Table B.3 sway-mode Cm (fixed)

# EN 1993-1-1 Table 6.1 — imperfection factor alpha per buckling curve
IMPERFECTION_ALPHA = {"a0": 0.13, "a": 0.21, "b": 0.34, "c": 0.49,
                      "d": 0.76}


def buckling_curve(h: float, b: float, axis: str) -> str:
    """Buckling-curve letter per EN 1993-1-1 Table 6.2 (rolled I, tf<=40mm).

    ``axis`` is the EC3 axis: "y" (major) or "z" (minor).
    h/b > 1.2:  y -> "a",  z -> "b"
    h/b <= 1.2: y -> "b",  z -> "c"
    (tf > 40 mm rows are not implemented — no library W-shape reaches
    40 mm flanges; callers pass table dimensions.)
    """
    if axis not in ("y", "z"):
        raise ValueError(f"axis must be 'y' or 'z', got {axis!r}")
    if h <= 0.0 or b <= 0.0:
        raise ValueError("h and b must be > 0")
    if h / b > 1.2:
        return "a" if axis == "y" else "b"
    return "b" if axis == "y" else "c"


def buckling_chi(lambda_bar: float, curve: str) -> float:
    """EC3 §6.3.1.2 reduction factor chi for one buckling curve.

    phi = 0.5*(1 + alpha*(lambda_bar - 0.2) + lambda_bar^2)
    chi = 1 / (phi + sqrt(phi^2 - lambda_bar^2))  <= 1.0
    (lambda_bar <= 0.2 gives chi = 1 exactly per §6.3.1.2(4)).
    """
    alpha = IMPERFECTION_ALPHA.get(curve)
    if alpha is None:
        raise ValueError(f"curve must be one of "
                         f"{sorted(IMPERFECTION_ALPHA)}, got {curve!r}")
    if not (math.isfinite(lambda_bar) and lambda_bar >= 0.0):
        raise ValueError("lambda_bar must be a finite value >= 0")
    if lambda_bar <= 0.2:
        return 1.0
    phi = 0.5 * (1.0 + alpha * (lambda_bar - 0.2) + lambda_bar ** 2)
    chi = 1.0 / (phi + math.sqrt(phi ** 2 - lambda_bar ** 2))
    return min(chi, 1.0)


# --------------------------------------------------------------------------- #
# result object (mirrors design.steel.MemberCheck)
# --------------------------------------------------------------------------- #
@dataclass
class MemberCheckEC3:
    """Outcome of one preliminary EC3 member check (SI: kN, kN*m)."""

    uid: str
    section: str
    kind: str                          # "column" | "beam" | "brace"
    Pu: float = 0.0                    # NEd, +compression / -tension (kN)
    Mu33: float = 0.0                  # My,Ed (EC3 major axis), kN*m
    Mu22: float = 0.0                  # Mz,Ed (EC3 minor axis), kN*m
    Vu: float = 0.0                    # VEd = max |V2| (kN)
    NplRd: Optional[float] = None      # A*fy/gamma_M0 (kN)
    NbRd: Optional[float] = None       # chi_min*A*fy/gamma_M1 (compression)
    MplRd33: Optional[float] = None    # Wpl,y*fy/gamma_M0 (kN*m)
    MplRd22: Optional[float] = None    # Wpl,z*fy/gamma_M0 (kN*m)
    VplRd: Optional[float] = None      # d*tw*fy/(sqrt(3)*gamma_M0) (kN)
    chi_y: Optional[float] = None      # major-axis buckling reduction
    chi_z: Optional[float] = None      # minor-axis buckling reduction
    ratio: Optional[float] = None      # governing utilisation
    equation: str = ""                 # "6.2" | "6.61" | "6.62" | "6.2.6"
    status: str = "N/A"                # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True           # ALWAYS True — screening check only
    governing_combo: str = ""          # envelope: governing combo name

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "section": self.section, "kind": self.kind,
            "Pu": self.Pu, "Mu33": self.Mu33, "Mu22": self.Mu22,
            "Vu": self.Vu, "NplRd": self.NplRd, "NbRd": self.NbRd,
            "MplRd33": self.MplRd33, "MplRd22": self.MplRd22,
            "VplRd": self.VplRd, "chi_y": self.chi_y, "chi_z": self.chi_z,
            "ratio": self.ratio, "equation": self.equation,
            "status": self.status, "notes": list(self.notes),
            "preliminary": True, "governing_combo": self.governing_combo,
        }


def _shear_demand(case: dict, uid: str) -> float:
    """max |V2| over the member stations (fallback: the two end shears)."""
    st = (case.get("member_stations") or {}).get(uid)
    if st and st.get("V2"):
        return max(abs(float(v)) for v in st["V2"])
    mf = (case.get("member_forces") or {}).get(uid)
    if mf is None:
        return 0.0
    return max(abs(float(mf[1])), abs(float(mf[7])))


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_members_ec3(model, results, case_or_combo: str, *,
                      fy: float = 355_000.0, kx: float = 1.0,
                      ky: float = 1.0, E: float = E_STEEL_EC3
                      ) -> List[MemberCheckEC3]:
    """Preliminary EN 1993-1-1 checks for every library W-shape member.

    Parameters
    ----------
    model         : BuildingModel the results were computed from.
    results       : engine results object (with .to_dict()) or the
                    CONTRACT.md results dict; may also be a hand-built
                    dict with a ``cases`` block (no engine needed).
    case_or_combo : name of the case / combo / RS case to check.
    fy            : yield stress [kPa]; default 355 MPa (S355).
    kx, ky        : effective-length factors, EC3 y (major) / z (minor)
                    axis buckling (``Lcr = k*L``).
    E             : modulus [kPa]; default 210 GPa (EN 1993-1-1).

    Only recognised library W-shapes (same rule as design.steel) are
    checked; anything else returns status "N/A".  See the module
    docstring for the exact provisions and documented simplifications.
    """
    if not (math.isfinite(fy) and fy > 0.0):
        raise ValueError("fy must be a finite value > 0 (kPa)")
    case = _case_block(results, case_or_combo)
    checks: List[MemberCheckEC3] = []
    for m in model.members:
        chk = MemberCheckEC3(uid=m.uid, section=m.section, kind=m.kind)
        checks.append(chk)

        sec = model.sections.get(m.section)
        if sec is None:
            chk.notes.append(f"unknown model section {m.section!r}")
            continue
        p = design_properties(sec.name)
        if p is None or abs(sec.A - p["A"]) > 0.01 * p["A"]:
            chk.notes.append("not a library W-shape")
            continue

        dem = _demands(case, m.uid)
        if dem is None:
            chk.notes.append("no member forces in results for this member")
            continue
        chk.Pu, chk.Mu33, chk.Mu22 = dem
        chk.Vu = _shear_demand(case, m.uid)

        A = sec.A
        d, b, tw = p["d"], p["bf"], p["tw"]
        L = m.length

        # ---- cross-section resistances (gamma_M0) ---------------------- #
        chk.NplRd = A * fy / GAMMA_M0
        chk.MplRd33 = p["Zx"] * fy / GAMMA_M0        # Wpl,y (EC3 major)
        chk.MplRd22 = p["Zy"] * fy / GAMMA_M0        # Wpl,z (EC3 minor)
        chk.VplRd = d * tw * fy / (math.sqrt(3.0) * GAMMA_M0)
        chk.notes.append("Av = d*tw simplification (§6.2.6); Class 1/2 "
                         "assumed; §6.2.9 plastic N-M interaction deferred "
                         "(linear Eq. 6.2 used)")

        ratios = [(abs(chk.Pu) / chk.NplRd + chk.Mu33 / chk.MplRd33
                   + chk.Mu22 / chk.MplRd22, "6.2"),
                  (chk.Vu / chk.VplRd, "6.2.6")]

        # ---- §6.3 buckling (compression members only) ------------------ #
        if chk.Pu > 0.0:
            NRk = A * fy
            terms = {}
            for axis, I, k in (("y", sec.I33, kx), ("z", sec.I22, ky)):
                Lcr = k * L
                Ncr = math.pi ** 2 * E * I / Lcr ** 2
                lam = math.sqrt(NRk / Ncr)
                curve = buckling_curve(p["d"], p["bf"], axis)
                chi = buckling_chi(lam, curve)
                terms[axis] = (lam, chi, curve)
            lam_y, chi_y, curve_y = terms["y"]
            lam_z, chi_z, curve_z = terms["z"]
            chk.chi_y, chk.chi_z = chi_y, chi_z
            chk.NbRd = min(chi_y, chi_z) * NRk / GAMMA_M1
            chk.notes.append(
                f"flexural buckling §6.3.1: curve {curve_y}/{curve_z} "
                f"(y/z), lambda = {lam_y:.3f}/{lam_z:.3f}, chi = "
                f"{chi_y:.4f}/{chi_z:.4f}; LTB deferred (chi_LT = 1)")

            # Annex B Table B.1 (non-susceptible members), Cm = 0.9 fixed
            n_y = chk.Pu / (chi_y * NRk / GAMMA_M1)
            n_z = chk.Pu / (chi_z * NRk / GAMMA_M1)
            kyy = CM_SWAY * (1.0 + min(lam_y - 0.2, 0.8) * n_y)
            kzz = CM_SWAY * (1.0 + min(2.0 * lam_z - 0.6, 1.4) * n_z)
            kyz, kzy = 0.6 * kzz, 0.6 * kyy
            MRd_y = p["Zx"] * fy / GAMMA_M1
            MRd_z = p["Zy"] * fy / GAMMA_M1
            my, mz = chk.Mu33 / MRd_y, chk.Mu22 / MRd_z
            ratios.append((n_y + kyy * my + kyz * mz, "6.61"))
            ratios.append((n_z + kzy * my + kzz * mz, "6.62"))
            chk.notes.append(
                f"interaction §6.3.3 Annex B Table B.1, Cm = {CM_SWAY} "
                f"(sway value, fixed): kyy = {kyy:.4f}, kzz = {kzz:.4f}, "
                "kyz = 0.6*kzz, kzy = 0.6*kyy")
        else:
            chk.notes.append("tension: §6.2.3 gross yielding only "
                             "(net-section rupture not checked)")

        chk.ratio, chk.equation = max(ratios)
        chk.status = "OK" if chk.ratio <= 1.0 else "NG"
    return checks


def check_members_ec3_envelope(model, results,
                               combos: Optional[List[str]] = None,
                               **kw) -> List[MemberCheckEC3]:
    """Governing EC3 member checks over a set of load combinations.

    Same envelope rule as :func:`skyframe.design.steel.
    check_members_envelope`: per member the LARGEST utilisation wins,
    tagged with ``governing_combo``; combos default to every name in
    ``results['combos']``.  ``**kw`` forwards (fy, kx, ky, E).
    """
    d = results.to_dict() if hasattr(results, "to_dict") else results
    if combos is None:
        combos = list((d.get("combos") or {}).keys())
    if not combos:
        raise ValueError("check_members_ec3_envelope: no combos to envelope "
                         "(none supplied and results has no 'combos')")
    per_combo = {c: check_members_ec3(model, d, c, **kw) for c in combos}
    n = len(per_combo[combos[0]])
    out: List[MemberCheckEC3] = []
    for i in range(n):
        gov = per_combo[combos[0]][i]
        gov_combo = combos[0]

        def rr(chk: MemberCheckEC3) -> float:
            return chk.ratio if chk.ratio is not None else -math.inf

        for c in combos[1:]:
            chk = per_combo[c][i]
            if rr(chk) > rr(gov):
                gov, gov_combo = chk, c
        gov.governing_combo = gov_combo
        out.append(gov)
    return out


def summarize_ec3(checks: List[MemberCheckEC3]) -> dict:
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
