"""CSI ETABS Verification, Analysis Example 7 -- Seven-story plane frame,
gravity and lateral loads (Rev. 2).  TIER 2: the static lateral results
(Table 7-1, 3 values) and the 7 periods (Table 7-2) are rebuilt; the
response-spectrum (Table 7-3) and time-history (Table 7-4) values are
BLOCKED (El Centro files not in the PDF).

Inputs (published, kip-in-s): column lines at X = 0, 30, 60 ft; stories
13'-6", 13'-6", then 5 @ 13'-0" (levels 0, 162, 324, 480, 636, 792, 948,
1104 in); fixed bases; columns W14 / beams W24 with the weights of Figure
7-1 (stories 1-3: 246/287/246, 4-5: 211/246/211, 6-7: 176/211/176;
beams levels 1-2 W24x160, 3-4 W24x130, 5-7 W24x110); story mass 0.49
kip-s^2/in; static lateral loads on the C1 side 2.5, 5, 7.5, 10, 12.5, 15,
20 k (1st .. roof).  References: Wilson & Habibullah (1992) SAP90 and
DYNAMIC/EASE2.

[assumed] Section A / I and E are NOT printed ("older sections ...
explicitly provided"): E = 29 500 ksi and the AISC tabulated strong-axis
values W14x176 (51.8 in^2, 2140 in^4), W14x211 (62.0, 2660), W14x246
(72.3, 3230), W14x287 (84.4, 3910), W24x110 (32.5, 3330), W24x130 (38.3,
4020), W24x160 (47.1, 5120).  With these an independent numpy frame gives
1.4514 in and T1 = 1.2734 s, i.e. ~0.05 % from the published values --
the residual is the unpublished section data, not the solver.
[assumed] Rigid floor (diaphragm) at every level, no shear deformation,
no end offsets; the static lateral case carries no gravity load (the C1
axial force 69.99 k is pure overturning).
"""

from __future__ import annotations

from ._core import (CaseResult, FIXED, engine, load, new_model, section,
                    support, tag_at, timed)
from ._svg import elevation

ID = "EX07"
TITLE = "Seven-story plane frame, static lateral + modes"
TIER = "2"

PROPS = {"W14x176": (51.8, 2140.0), "W14x211": (62.0, 2660.0),
         "W14x246": (72.3, 3230.0), "W14x287": (84.4, 3910.0),
         "W24x110": (32.5, 3330.0), "W24x130": (38.3, 4020.0),
         "W24x160": (47.1, 5120.0)}
LEVELS = [0.0, 162.0, 324.0, 480.0, 636.0, 792.0, 948.0, 1104.0]
EXT = ["W14x246"] * 3 + ["W14x211"] * 2 + ["W14x176"] * 2
INT = ["W14x287"] * 3 + ["W14x246"] * 2 + ["W14x211"] * 2
BEAM = ["W24x160"] * 2 + ["W24x130"] * 2 + ["W24x110"] * 3
FLAT = [2.5, 5.0, 7.5, 10.0, 12.5, 15.0, 20.0]
REF_STATIC = [("Lateral displacement at roof", "in", "1.4508", "1.4508"),
              ("Axial force, column C1 at ground", "kip", "69.99", "69.99"),
              ("Moment, column C1 at ground", "kip-in", "2324.68",
               "2324.68")]
REF_T = [("1.27321", "1.27321"), ("0.43128", "0.43128"),
         ("0.24205", "0.24204"), ("0.16018", "0.16018"),
         ("0.11899", "0.11899"), ("0.09506", "0.09506"),
         ("0.07952", "0.07951")]       # (ETABS, W&H)
BLOCKED = [
    "Table 7-3 RS (roof 5.4314 in, C1 axial 261.50 k, C1 moment 9916.11 "
    "kip-in, W&H CQC): needs the El Centro spectrum ordinates (ELCN-RS1).",
    "Table 7-4 TH maxima over 8 s (roof 5.48 in, base shear 284 k, C1 "
    "axial 258 k, moment 8740 kip-in, W&H): needs the ELCN-THU record "
    "(time step and format not in the PDF; TH damping not stated).",
    "Exact section A / I and E (published only in the SAP90 manual / "
    "Example 07.EDB): AISC values reproduce the frame to ~0.05 %."]


def build():
    names = ["1st", "2nd", "3rd", "4th", "5th", "6th", "Roof"]
    m = new_model("CSI Ex07", 29500.0,
                  heights=[LEVELS[k + 1] - LEVELS[k] for k in range(7)],
                  names=names, diaphragm=True)
    m.active_dof = ["UX", "UZ", "RY"]
    for n, (A, I) in PROPS.items():
        section(m, n, A, I)
    for k in range(7):
        z0, z1 = LEVELS[k], LEVELS[k + 1]
        st = names[k]
        for i, (x, sec) in enumerate(((0.0, EXT[k]), (360.0, INT[k]),
                                      (720.0, EXT[k]))):
            m.add_member("column", sec, (x, 0, z0), (x, 0, z1), story=st,
                         uid=f"C{i + 1}_{k + 1}")
        for b in range(2):
            m.add_member("beam", BEAM[k], (360.0 * b, 0, z1),
                         (360.0 * (b + 1), 0, z1), story=st,
                         uid=f"B{b + 1}_{k + 1}")
        load(m, "LAT", (0.0, 0.0, z1), fx=FLAT[k])
        m.story_masses[st] = 0.49
    for x in (0.0, 360.0, 720.0):
        support(m, (x, 0, 0), FIXED)
    m.add_case("LAT", {"LAT": 1.0})
    m.num_modes = 7
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER, status="partial",
        source="Wilson & Habibullah (1992) SAP90; DYNAMIC/EASE2; CSI ETABS "
               "Verification Example 7, Tables 7-1 and 7-2",
        tests="2D frame, user sections, static lateral loads, 7 modes "
              "(RS and TH blocked)")
    res.inputs = [("Units", "kip, in, s"),
                  ("Geometry", "2 bays x 30 ft; 2 x 13.5 ft + 5 x 13 ft"),
                  ("Sections", "W14 columns, W24 beams (Figure 7-1)"),
                  ("Mass", "0.49 kip-s2/in per level"),
                  ("Lateral", "2.5/5/7.5/10/12.5/15/20 k at levels 1..R")]
    res.assumptions = [
        "[assumed] E = 29,500 ksi and AISC A / I of the listed W shapes "
        "(not printed in the PDF).",
        "[assumed] Rigid floor, no shear deformation, no end offsets; "
        "column strong axis in the frame plane.",
    ]
    res.differences = [
        "Example 7: the section properties CSI used are unpublished; with "
        "AISC values SkyFrame (like an independent numpy frame) lands "
        "~0.05 % from the reference -- an input gap, not a solver gap."]
    res.blocked = list(BLOCKED)
    m = build()
    # column strong axis in the X-Z plane (SkyFrame legacy local axes)
    for mem in m.members:
        if mem.kind == "column":
            mem.angle = 90.0
    eng = engine(m)
    r = eng.run_static("LAT")
    roof = r.node_disp[eng._asm.masters["Roof"]][0]
    rc = r.reactions[tag_at(eng, (0.0, 0.0, 0.0))]
    vals = [abs(roof), abs(rc[2]), abs(rc[4])]
    for (q, u, et, ref), v in zip(REF_STATIC, vals):
        res.add(q, "", u, ref, "Wilson & Habibullah 1992", et, float(v),
                cls="internal" if "column" in q else "global",
                group="Static lateral")
    md = eng.run_modal(7)
    for k, (et, ref) in enumerate(REF_T):
        res.add(f"Period, mode {k + 1}", "", "s", ref,
                "Wilson & Habibullah 1992", et, float(md.periods[k]),
                group="Periods")
    sk = elevation([0, 360, 720], LEVELS[1:])
    for z, f in zip(LEVELS[1:], FLAT):
        sk.arrow((0, z), (1, 0), f"{f:g}k")
    res.svg = sk.render("Example 7 elevation")
    return res
