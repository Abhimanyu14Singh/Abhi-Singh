"""CSI ETABS Verification, Analysis Example 3 -- Three-story plane frame,
code-specified static lateral loads (Rev. 2): UBC 1997 seismic, ASCE 7-02
seismic and UBC 1997 wind story shears (Tables 3-1 .. 3-3, 9 values).

Inputs (published, kip-in-s): the Example 2 frame (2 column lines 36 ft
apart, 3 stories of 12 ft, fixed bases), columns W14X90, E = 29 500 ksi,
nu = 0.3, beams infinitely rigid and 24 in deep, story mass 0.4
kip-s^2/in.  UBC97 seismic: Z = 0.40, soil SC, I = 1.25, R = 8.5, Ct =
0.035, source type B at 15 km.  ASCE 7-02: site C, Ss = 1, S1 = 0.4, R =
8, Ct = 0.035, seismic use group I.  UBC97 wind: frame width 20 ft, 100
mph, exposure B, I = 1, Cq 0.8 windward / 0.5 leeward.  Reference: hand
calculation from the code formulas with the ETABS period 0.5204 s.

The SkyFrame code generators work in SI, so this model is built in kN,
m, tonne (exact unit conversions) and the story shears are converted back
to kips.  The SkyFrame period from its own modal analysis is passed to
the generators as the program period (ETABS does the same).

[assumed] W14X90 from the AISC database (not printed): A = 26.5 in^2,
I_x = 999 in^4, I_y = 362 in^4, J = 4.06 in^4, d = 14.0 in, t_w = 0.440
in; strong-axis shear area d t_w = 6.16 in^2 (shear deformation included,
axial included).  [assumed] Rigid end offset 24 in at the column tops as
in Example 2 (ETABS computes it from the beam depth).
[derived] Seismic weight W = 3 x 0.4 kip-s^2/in x g: SkyFrame (like ETABS)
uses g = 9.80665 m/s^2 = 386.09 in/s^2, the hand calculation 386.4 --
this alone puts the seismic shears 0.08 % below the theoretical column
(ETABS: 68.13 vs 68.19).
[derived] UBC97: Na = Nv = 1.0 (source type B at 15 km, Tables 16-S/T),
so Ca = 0.40, Cv = 0.56 (soil SC, zone 4); the 2.5 Ca I W / R cap governs.
[assumed] UBC97 wind convention (not documented in the PDF): windward
Ce at the level elevation, leeward Ce at the roof height, tributary half
stories (see ``skyframe.core.codes_legacy_ext``).  The published forces
are not reproduced exactly by any of the conventions tried (level /
integrated / mid-strip Ce, leeward at h or z); the residual is reported.
"""

from __future__ import annotations

from skyframe.core import autolateral as al

from ._core import (CaseResult, FIXED, engine, new_model, section, support,
                    timed)
from ._svg import elevation

ID = "EX03"
TITLE = "Three-story frame, UBC97 / ASCE 7-02 seismic and UBC97 wind"
TIER = "2"

IN = 0.0254
KIP = 4.4482216152605
KSI = KIP / IN ** 2
MASS = KIP / IN          # kip-s^2/in -> tonne (kN s^2/m)

REF = {   # code -> [(level, ETABS, theoretical)] story shears in kips
    "ubc97": [("Roof", "34.07", "34.09"), ("2nd", "56.78", "56.82"),
              ("1st", "68.13", "68.19")],
    "asce7_02": [("Roof", "19.37", "19.38"), ("2nd", "32.23", "32.25"),
                 ("1st", "38.61", "38.64")],
    "ubc97_wind": [("Roof", "3.30", "3.30"), ("2nd", "9.49", "9.49"),
                   ("1st", "15.21", "15.21")],
}
LABEL = {"ubc97": "UBC 1997 seismic", "asce7_02": "ASCE 7-02 seismic",
         "ubc97_wind": "UBC 1997 wind"}
ETABS_T = "0.5204"


def build():
    h = 144.0 * IN
    m = new_model("CSI Ex03", 29500.0 * KSI, nu=0.3, heights=[h] * 3,
                  names=["1st", "2nd", "Roof"], diaphragm=True)
    m.active_dof = ["UX", "UZ", "RY"]
    sec = section(m, "W14X90", 26.5 * IN ** 2, 999.0 * IN ** 4,
                  362.0 * IN ** 4, 4.06 * IN ** 4)
    sec.As2 = 14.0 * 0.440 * IN ** 2
    sec.As3 = 5.0 / 6.0 * 2 * 14.5 * 0.710 * IN ** 2
    section(m, "RB", 1e3, 1e3, 1e3, 1e3)
    L = 432.0 * IN
    for k in range(3):
        st = m.stories[k].name
        z0, z1 = h * k, h * (k + 1)
        for i, x in enumerate((0.0, L)):
            m.add_member("column", "W14X90", (x, 0, z0), (x, 0, z1),
                         story=st, uid=f"C{i + 1}_{k + 1}",
                         rigid_j=24.0 * IN, angle=90.0)
        m.add_member("beam", "RB", (0, 0, z1), (L, 0, z1), story=st,
                     uid=f"B1_{k + 1}")
        m.story_masses[st] = 0.4 * MASS
    for x in (0.0, L):
        support(m, (x, 0, 0), FIXED)
    m.num_modes = 3
    return m


def generate(m, T: float) -> None:
    al.generate(m, "ubc97", "UBC97", R=8.5, Z=0.4, soil="SC", I=1.25,
                Na=1.0, Nv=1.0, T=T, structure="steel_mrf")
    al.generate(m, "asce7_02", "ASCE02", Ss=1.0, S1=0.4, R=8.0, I=1.0,
                site="C", Ct=0.035 * (1 / 0.3048) ** 0.75, x=0.75, T=T)
    al.generate(m, "ubc97_wind", "WUBC", V=100 * 0.44704, exposure="B",
                Iw=1.0, cq_windward=0.8, cq_leeward=0.5, width=20 * 0.3048)


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="Hand calculation from UBC 1997 / ASCE 7-02 formulas with "
               "the ETABS period; CSI ETABS Verification Example 3, Tables "
               "3-1 .. 3-3",
        tests="Code auto-lateral generators (UBC97 seismic, ASCE 7-02 ELF, "
              "UBC97 wind) and story shears")
    res.inputs = [("Units", "kip, in, s (run in kN, m)"),
                  ("Frame", "Example 2 frame, W14X90, E 29,500 ksi"),
                  ("Mass", "0.4 kip-s2/in per story"),
                  ("UBC97", "Z 0.40, SC, I 1.25, R 8.5, Ct 0.035, B @ "
                            "15 km"),
                  ("ASCE 7-02", "Site C, Ss 1, S1 0.4, R 8, Ct 0.035, SUG "
                                "I"),
                  ("UBC97 wind", "100 mph, exp. B, I 1, Cq 0.8 / 0.5, "
                                 "width 20 ft")]
    res.assumptions = [
        "[assumed] W14X90 AISC properties incl. shear area d tw (not "
        "printed); 24 in rigid offsets at the column tops as Example 2.",
        "[derived] g = 386.09 in/s2 (SkyFrame and ETABS) vs 386.4 in the "
        "hand calculation: -0.08 % on every seismic shear.",
        "[derived] Na = Nv = 1.0 (source B at 15 km) -> Ca 0.40, Cv 0.56.",
        "[assumed] UBC97 wind: windward Ce at the level, leeward Ce at "
        "roof height, half-story tributary (convention not documented).",
        "The SkyFrame modal period is passed as the program period.",
    ]
    res.differences = [
        "Example 3 seismic: -0.08 % is the gravity constant (386.09 vs "
        "386.4 in/s2), identical to ETABS's own offset.",
        "Example 3 wind: the UBC97 exposure / tributary convention behind "
        "the published forces is not documented and was not reproduced "
        "by any convention tried (level, integrated or mid-strip Ce; "
        "leeward at h or at z); SkyFrame's code-literal convention lands "
        "2-4 % low.  Treat the wind rows as a convention gap, not a "
        "solver result."]
    m = build()
    eng = engine(m)
    T = float(eng.run_modal(1).periods[0])
    generate(m, T)
    for pat in ("UBC97", "ASCE02", "WUBC"):
        m.add_case(pat, {pat: 1.0})
    eng = engine(m)
    for code, pat in (("ubc97", "UBC97"), ("asce7_02", "ASCE02"),
                      ("ubc97_wind", "WUBC")):
        r = eng.run_static(pat)
        for lvl, et, th in REF[code]:
            v = r.story[lvl]["shear_x"] / KIP
            res.add(f"Story shear, {LABEL[code]}", lvl, "kip", th,
                    "Hand calculation (code formulas)", et, float(v),
                    group=LABEL[code])
        base = -r.base["FX"] / KIP
        res.rows[-1].note = f"base reaction {base:.10g} kip"
    res.inputs.append(("SkyFrame period", f"{T:.4f} s (ETABS {ETABS_T})"))
    sk = elevation([0, 432], [144, 288, 432])
    res.svg = sk.render("Example 3 elevation")
    return res
