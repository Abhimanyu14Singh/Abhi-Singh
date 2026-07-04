"""PRELIMINARY AISC 360-16 elastic design checks for steel W-shape members.

This module screens SkyFrame frame members (W-shapes from the built-in
section library, :mod:`skyframe.core.sections_library`) against the AISC
360-16 LRFD provisions:

* Chapter E  (E3)     — flexural buckling of compression members
* Chapter D  (D2a)    — tension yielding on the gross section (yielding ONLY;
                        rupture/net-section and connection limit states are
                        NOT checked)
* Chapter F  (F2)     — major-axis flexure: yielding + lateral-torsional
                        buckling of doubly symmetric compact members,
                        conservatively with Cb = 1.0
* Chapter F  (F6)     — minor-axis flexure: Mn = min(Fy*Zy, 1.6*Fy*Sy)
* Chapter H  (H1-1a/b)— combined axial force + biaxial bending interaction

THESE ARE PRELIMINARY SCREENING CHECKS, NOT A FINAL CODE CHECK.  In
particular the module does NOT classify local slenderness (compact /
noncompact / slender webs and flanges are all treated as compact, a note
is emitted where the flange is noncompact at the given Fy), does not
apply Cb > 1, second-order amplification (B1/B2), shear (Chapter G),
torsion (Chapter H3), serviceability, or connection design.  Every result
carries ``"preliminary": True`` to make this explicit.

Units: SkyFrame SI throughout — kN, m, kPa (so Fy = 345 MPa is passed as
345_000.0 kPa).  Design modulus of elasticity is E = 200 GPa (2.0e8 kPa).

Sign conventions (verified empirically against the OpenSees engine — see
tests/test_design.py): the engine's ``member_forces[uid][0]`` (local N at
end i) is POSITIVE for a member in compression, so ``Pu`` here is
+compression / -tension.  Station axial values (``member_stations`` "N")
use the opposite, internal tension-positive convention and are NOT used
for Pu.  Moments are taken as maximum absolute values, so their sign
convention is irrelevant.

W-shape design table: the analysis library
(:mod:`skyframe.core.sections_library`) stores only A/I33/I22/J, so this
module carries its own self-contained table of the additional properties
needed for design (d, bf, tw, tf, Zx, Zy, rx, ry, rts) for the same 22
shapes, from the AISC Steel Construction Manual (15th ed.) Table 1-1 /
AISC Shapes Database v15.0, converted exactly from the published imperial
values (1 in = 0.0254 m exactly).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = ["MemberCheck", "check_members", "check_members_envelope",
           "summarize", "E_STEEL"]

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
E_STEEL = 200_000_000.0     # kPa (200 GPa) — design modulus, AISC E = 29 000 ksi
PHI_T = 0.9                 # tension yielding resistance factor (D2a)

_IN = 0.0254                # m per inch (exact)
_IN2 = _IN ** 2             # m^2 per in^2
_IN3 = _IN ** 3             # m^3 per in^3

# --------------------------------------------------------------------------- #
# W-shape design property table
# --------------------------------------------------------------------------- #
# Source: AISC Steel Construction Manual (15th ed.) Table 1-1 / AISC Shapes
# Database v15.0 — published IMPERIAL values, converted on access with the
# exact factor 1 in = 0.0254 m.  The same 22 shapes as
# skyframe.core.sections_library; ``A`` is repeated here purely to VALIDATE
# (within 1%) that a model section claiming a library name really carries
# the library properties.
#
# label: ( A ,  d  ,  bf ,  tw  ,  tf  ,  Zx ,  Zy ,  rx ,  ry , rts )
#         in^2  in    in    in     in    in^3  in^3   in    in    in
_W_DESIGN_IMPERIAL: Dict[str, tuple] = {
    "W8x31":   (9.13,  8.00,  8.00, 0.285, 0.435,  30.4, 14.1,  3.47, 2.02, 2.26),
    "W8x40":   (11.7,  8.25,  8.07, 0.360, 0.560,  39.8, 18.5,  3.53, 2.04, 2.31),
    "W10x33":  (9.71,  9.73,  7.96, 0.290, 0.435,  38.8, 14.0,  4.19, 1.94, 2.20),
    "W10x49":  (14.4, 10.0,  10.0,  0.340, 0.560,  60.4, 28.3,  4.35, 2.54, 2.84),
    "W12x26":  (7.65, 12.2,   6.49, 0.230, 0.380,  37.2,  8.17, 5.17, 1.51, 1.75),
    "W12x40":  (11.7, 11.9,   8.01, 0.295, 0.515,  57.0, 16.8,  5.13, 1.94, 2.21),
    "W12x65":  (19.1, 12.1,  12.0,  0.390, 0.605,  96.8, 44.1,  5.28, 3.02, 3.38),
    "W14x30":  (8.85, 13.8,   6.73, 0.270, 0.385,  47.3,  8.99, 5.73, 1.49, 1.77),
    "W14x48":  (14.1, 13.8,   8.03, 0.340, 0.595,  78.4, 19.6,  5.85, 1.91, 2.20),
    "W14x90":  (26.5, 14.0,  14.5,  0.440, 0.710, 157.0, 75.6,  6.14, 3.70, 4.10),
    "W16x36":  (10.6, 15.9,   6.99, 0.295, 0.430,  64.0, 10.8,  6.51, 1.52, 1.83),
    "W16x57":  (16.8, 16.4,   7.12, 0.430, 0.715, 105.0, 18.9,  6.72, 1.60, 1.92),
    "W18x50":  (14.7, 18.0,   7.50, 0.355, 0.570, 101.0, 16.6,  7.38, 1.65, 1.98),
    "W18x76":  (22.3, 18.2,  11.0,  0.425, 0.680, 163.0, 42.2,  7.73, 2.61, 3.02),
    "W21x62":  (18.3, 21.0,   8.24, 0.400, 0.615, 144.0, 21.7,  8.54, 1.77, 2.15),
    "W21x93":  (27.3, 21.6,   8.42, 0.580, 0.930, 221.0, 34.7,  8.70, 1.84, 2.24),
    "W24x76":  (22.4, 23.9,   8.99, 0.440, 0.680, 200.0, 28.6,  9.69, 1.92, 2.33),
    "W24x104": (30.7, 24.1,  12.8,  0.500, 0.750, 289.0, 62.4, 10.1,  2.91, 3.42),
    "W27x94":  (27.6, 26.9,  10.0,  0.490, 0.745, 278.0, 38.8, 10.9,  2.12, 2.59),
    "W30x116": (34.2, 30.0,  10.5,  0.565, 0.850, 378.0, 49.2, 12.0,  2.19, 2.70),
    "W33x130": (38.3, 33.1,  11.5,  0.580, 0.855, 467.0, 59.5, 13.2,  2.39, 2.94),
    "W36x150": (44.3, 35.9,  12.0,  0.625, 0.940, 581.0, 70.9, 14.3,  2.47, 3.06),
}


def design_properties(name: str) -> Optional[dict]:
    """SI design properties for one library W-shape, or None if unknown.

    Keys: A [m^2]; d, bf, tw, tf, rx, ry, rts [m]; Zx, Zy [m^3].
    """
    row = _W_DESIGN_IMPERIAL.get(name)
    if row is None:
        return None
    A, d, bf, tw, tf, Zx, Zy, rx, ry, rts = row
    return {
        "A": A * _IN2,
        "d": d * _IN, "bf": bf * _IN, "tw": tw * _IN, "tf": tf * _IN,
        "Zx": Zx * _IN3, "Zy": Zy * _IN3,
        "rx": rx * _IN, "ry": ry * _IN, "rts": rts * _IN,
    }


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #
@dataclass
class MemberCheck:
    """Outcome of one preliminary member design check (all SI: kN, kN*m)."""

    uid: str
    section: str
    kind: str                          # "column" | "beam" | "brace"
    Pu: float = 0.0                    # kN, +compression / -tension
    Mu33: float = 0.0                  # kN*m, max |M3| over stations (or ends)
    Mu22: float = 0.0                  # kN*m, max |M2| over stations (or ends)
    phiPn: Optional[float] = None      # kN   (phi_c*Pn compression, PHI_T*Pn tension)
    phiMn33: Optional[float] = None    # kN*m (phi_b * F2 capacity)
    phiMn22: Optional[float] = None    # kN*m (phi_b * F6 capacity)
    ratio: Optional[float] = None      # governing H1 interaction value
    equation: str = ""                 # "H1-1a" | "H1-1b" | ""
    status: str = "N/A"                # "OK" | "NG" | "N/A"
    notes: List[str] = field(default_factory=list)
    preliminary: bool = True           # ALWAYS True — screening check only
    governing_combo: str = ""          # v0.9 envelope: governing combo name

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "section": self.section, "kind": self.kind,
            "Pu": self.Pu, "Mu33": self.Mu33, "Mu22": self.Mu22,
            "phiPn": self.phiPn, "phiMn33": self.phiMn33,
            "phiMn22": self.phiMn22, "ratio": self.ratio,
            "equation": self.equation, "status": self.status,
            "notes": list(self.notes), "preliminary": True,
            "governing_combo": self.governing_combo,
        }


# --------------------------------------------------------------------------- #
# capacity building blocks (module-level so tests can exercise them directly)
# --------------------------------------------------------------------------- #
def _compression_capacity(A: float, KLr: float, Fy: float,
                          E: float = E_STEEL) -> tuple:
    """AISC E3 flexural-buckling Pn [kN] and branch label.

    Fe = pi^2 E / (KL/r)^2                                (E3-4)
    KL/r <= 4.71 sqrt(E/Fy):  Fcr = 0.658^(Fy/Fe) Fy      (E3-2, inelastic)
    else:                     Fcr = 0.877 Fe              (E3-3, elastic)
    """
    Fe = math.pi ** 2 * E / KLr ** 2
    if KLr <= 4.71 * math.sqrt(E / Fy):
        Fcr = 0.658 ** (Fy / Fe) * Fy
        branch = "E3-2 (inelastic)"
    else:
        Fcr = 0.877 * Fe
        branch = "E3-3 (elastic)"
    return Fcr * A, branch


def _major_axis_capacity(Lb: float, Fy: float, Zx: float, Sx: float,
                         ry: float, rts: float, J: float, ho: float,
                         E: float = E_STEEL, Cb: float = 1.0) -> tuple:
    """AISC F2 major-axis Mn [kN*m] (yielding + LTB, c = 1) and branch label.

    Mp = Fy Zx                                            (F2-1)
    Lp = 1.76 ry sqrt(E/Fy)                               (F2-5)
    Lr per F2-6; Lb <= Lp: Mn = Mp; Lp < Lb <= Lr: linear
    interpolation (F2-2); Lb > Lr: elastic LTB (F2-3, F2-4).
    """
    Mp = Fy * Zx
    Lp = 1.76 * ry * math.sqrt(E / Fy)
    if Lb <= Lp:
        return Mp, "F2-1 (yielding)"
    c = 1.0                                    # doubly symmetric I-shape (F2-8a)
    jc = J * c / (Sx * ho)
    Lr = 1.95 * rts * E / (0.7 * Fy) * math.sqrt(
        jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * Fy / E) ** 2))       # (F2-6)
    if Lb <= Lr:
        Mn = Cb * (Mp - (Mp - 0.7 * Fy * Sx) * (Lb - Lp) / (Lr - Lp))
        return min(Mn, Mp), "F2-2 (inelastic LTB)"
    slend = (Lb / rts) ** 2
    Fcr = Cb * math.pi ** 2 * E / slend * math.sqrt(1.0 + 0.078 * jc * slend)
    return min(Fcr * Sx, Mp), "F2-3 (elastic LTB)"                  # (F2-3/4)


def _minor_axis_capacity(Fy: float, Zy: float, Sy: float) -> float:
    """AISC F6 minor-axis yielding: Mn = min(Fy Zy, 1.6 Fy Sy) [kN*m]."""
    return min(Fy * Zy, 1.6 * Fy * Sy)


# --------------------------------------------------------------------------- #
# demand extraction
# --------------------------------------------------------------------------- #
def _case_block(results, case_or_combo: str) -> dict:
    """The per-case results dict for a case, combo, or RS case name.

    Accepts a results OBJECT (anything with .to_dict()) or an already
    JSON-shaped dict per CONTRACT.md.
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


def _demands(case: dict, uid: str):
    """(Pu, Mu33, Mu22) for one member, or None if it has no forces.

    Pu = local N at end i from member_forces (POSITIVE = compression —
    engine convention, verified empirically in tests/test_design.py).
    Mu33/Mu22 = max |M3| / |M2| over member_stations when present, else
    over the two ends from member_forces
    ([Ni,Vyi,Vzi,Ti,Myi,Mzi, Nj,Vyj,Vzj,Tj,Myj,Mzj] -> Mz=M3, My=M2).
    """
    mf = (case.get("member_forces") or {}).get(uid)
    if mf is None:
        return None
    Pu = float(mf[0])
    st = (case.get("member_stations") or {}).get(uid)
    if st and st.get("M3"):
        Mu33 = max(abs(float(v)) for v in st["M3"])
        Mu22 = max(abs(float(v)) for v in st.get("M2") or [0.0])
    else:
        Mu33 = max(abs(float(mf[5])), abs(float(mf[11])))
        Mu22 = max(abs(float(mf[4])), abs(float(mf[10])))
    return Pu, Mu33, Mu22


# --------------------------------------------------------------------------- #
# main API
# --------------------------------------------------------------------------- #
def check_members(model, results, case_or_combo: str, *,
                  Fy: float = 345_000.0, kx: float = 1.0, ky: float = 1.0,
                  Lb: Optional[float] = None, phi_b: float = 0.9,
                  phi_c: float = 0.9) -> List[MemberCheck]:
    """Preliminary AISC 360-16 LRFD checks for every W-shape frame member.

    Parameters
    ----------
    model         : BuildingModel the results were computed from.
    results       : engine results object (with .to_dict()) or the
                    CONTRACT.md results dict; may also be a hand-built dict
                    with a ``cases`` block (no engine needed).
    case_or_combo : name of the case / combo / RS case to check.
    Fy            : yield stress [kPa]; default 345 MPa (A992, 50 ksi).
    kx, ky        : effective-length factors about the major / minor axis.
    Lb            : laterally unbraced compression-flange length [m] applied
                    to ALL members; None = each member's own length.
    phi_b, phi_c  : LRFD resistance factors for flexure / compression.

    Only members whose section is a recognised library W-shape (name in the
    design table AND model-section area within 1% of the published value)
    are checked; anything else returns status "N/A".  Members with tensile
    Pu are checked with D2 gross-yielding Pn (phi_t = 0.9).  Results are
    PRELIMINARY (see module docstring).
    """
    case = _case_block(results, case_or_combo)
    checks: List[MemberCheck] = []
    for m in model.members:
        chk = MemberCheck(uid=m.uid, section=m.section, kind=m.kind)
        checks.append(chk)

        sec = model.sections.get(m.section)
        if sec is None:
            chk.notes.append(f"unknown model section {m.section!r}")
            continue
        # Identify a library W-shape by NAME + AREA validation (the library
        # sections carry drawing b/h = bf/d, so b/h cannot distinguish them;
        # a rectangular/user section fails the name or the 1% area check).
        p = design_properties(sec.name)
        if p is None or abs(sec.A - p["A"]) > 0.01 * p["A"]:
            chk.notes.append("not a library W-shape")
            continue

        dem = _demands(case, m.uid)
        if dem is None:
            chk.notes.append("no member forces in results for this member")
            continue
        chk.Pu, chk.Mu33, chk.Mu22 = dem

        L = m.length
        Lb_m = L if Lb is None else float(Lb)
        A = sec.A                                # m^2 (validated vs table)
        d, bf, tf = p["d"], p["bf"], p["tf"]
        Sx = sec.I33 / (d / 2.0)                 # m^3 (elastic, from model I33)
        Sy = sec.I22 / (bf / 2.0)                # m^3
        ho = d - tf                              # distance between flange centroids

        # ---- axial capacity ------------------------------------------- #
        KLr = max(kx * L / p["rx"], ky * L / p["ry"])
        if chk.Pu >= 0.0:                        # compression (or no axial)
            Pn, branch = _compression_capacity(A, KLr, Fy)
            chk.phiPn = phi_c * Pn
            chk.notes.append(f"compression {branch}, KL/r={KLr:.1f}")
            if KLr > 200.0:
                chk.notes.append("KL/r > 200 (AISC E2 user note)")
        else:                                    # tension
            chk.phiPn = PHI_T * Fy * A
            chk.notes.append("tension: D2 gross yielding only "
                             "(rupture/connections not checked)")

        # ---- flexural capacities -------------------------------------- #
        Mn33, fbranch = _major_axis_capacity(
            Lb_m, Fy, p["Zx"], Sx, p["ry"], p["rts"], sec.J, ho)
        chk.phiMn33 = phi_b * Mn33
        chk.notes.append(f"major-axis flexure {fbranch}, Cb=1.0, "
                         f"Lb={Lb_m:.3g} m")
        chk.phiMn22 = phi_b * _minor_axis_capacity(Fy, p["Zy"], Sy)
        if bf / (2.0 * tf) > 0.38 * math.sqrt(E_STEEL / Fy):
            chk.notes.append("flange noncompact at this Fy — FLB not "
                             "checked (preliminary)")

        # ---- H1 interaction ------------------------------------------- #
        Pr = abs(chk.Pu)
        pr_pc = Pr / chk.phiPn
        m_term = chk.Mu33 / chk.phiMn33 + chk.Mu22 / chk.phiMn22
        if pr_pc >= 0.2:
            chk.ratio = pr_pc + (8.0 / 9.0) * m_term                # H1-1a
            chk.equation = "H1-1a"
        else:
            chk.ratio = pr_pc / 2.0 + m_term                        # H1-1b
            chk.equation = "H1-1b"
        chk.status = "OK" if chk.ratio <= 1.0 else "NG"
    return checks


def check_members_envelope(model, results, combos: Optional[List[str]] = None,
                           **kw) -> List[MemberCheck]:
    """Governing AISC 360 member checks over a set of load combinations.

    Runs :func:`check_members` for every combo in ``combos`` (default: every
    name in ``results['combos']``) and returns, per member, the check with
    the LARGEST interaction ratio, tagged with ``governing_combo``.  A member
    that is "N/A" (no ratio) in every combo keeps the first combo's check.
    ``**kw`` is forwarded unchanged (Fy, kx, ky, Lb, phi_b, phi_c).  A
    single-combo envelope equals that combo's checks (plus the tag).
    """
    d = results.to_dict() if hasattr(results, "to_dict") else results
    if combos is None:
        combos = list((d.get("combos") or {}).keys())
    if not combos:
        raise ValueError("check_members_envelope: no combos to envelope "
                         "(none supplied and results has no 'combos')")
    per_combo = {c: check_members(model, d, c, **kw) for c in combos}
    n = len(per_combo[combos[0]])
    out: List[MemberCheck] = []
    for i in range(n):
        gov = per_combo[combos[0]][i]
        gov_combo = combos[0]

        def rr(chk: MemberCheck) -> float:
            return chk.ratio if chk.ratio is not None else -math.inf

        for c in combos[1:]:
            chk = per_combo[c][i]
            if rr(chk) > rr(gov):
                gov, gov_combo = chk, c
        gov.governing_combo = gov_combo
        out.append(gov)
    return out


def summarize(checks: List[MemberCheck]) -> dict:
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
