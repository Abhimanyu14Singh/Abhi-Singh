"""CSI ETABS Verification Examples 9-14: what blocks a rebuild from the
published PDFs.

Every one of these examples is within SkyFrame's feature set (3D frames,
rigid / multiple diaphragms, RS with angled excitation, nonlinear links:
hysteretic dampers, viscous dampers, gaps, biaxial hysteretic and friction
pendulum isolators, nonlinear time history).  None can be compared with
the published values from the PDFs alone; the exact missing items are
listed per example (checked against each PDF).  Examples 2, 5 and 7 are
partly blocked too (see their own modules).
"""

from __future__ import annotations

from ._core import CaseResult

TIER = "blocked"
TITLE = "Blocked (inputs not published)"

BLOCKED = {
    "EX09": ("Two-story 3D unsymmetrical frame, RS at 30 / 120 deg",
             "Wilson & Habibullah (1992) SAP90; Table 9-1 (6 periods, 2 "
             "CM displacements)",
             ["Story masses, mass moments of inertia and centre-of-mass "
              "coordinates (Figure 9-1 marks the CM but gives no value).",
              "Response-spectrum ordinates, damping and modal / "
              "directional combination (\"identical to Wilson and "
              "Habibullah (1992)\", not reproduced).",
              "Plan dimensions of B1 / B2 (only B3, B4, B5 carry 25 ft) "
              "and whether rigid offsets or self-mass are used.",
              "Units of the two displacements (kip-ft model, presumably "
              "ft).",
              "Available: E = 432,000 ksf, 18x18 in columns, 12x24 in "
              "beams, 2 stories of 12.5 ft -- the periods become "
              "checkable as soon as the masses are known."]),
    "EX10": ("3-story plane frame with ADAS hysteretic dampers, NLTH",
             "DRAIN-2DX; Table 10-1 (18 values, two damper sizes)",
             ["Column, beam and brace section properties (\"user "
              "defined\", values not printed).",
              "ADAS (PLASTIC1) link stiffness, yield force, exponent and "
              "post-yield data per story (\"manufacturer supplied\").",
              "Story masses, inherent damping and number of Ritz vectors.",
              "El Centro 1940 N-S record file ELCN-THE (digitisation, dt, "
              "scale, duration)."]),
    "EX11": ("3-story plane frame with viscous dampers, NLTH",
             "DRAIN-2DX; Table 11-1 (18 values, two damper sizes)",
             ["Column, beam and brace section properties.",
              "Damper coefficient C per story and the damper link series "
              "stiffness k (alpha = 1 is given).",
              "Story masses, inherent damping and number of Ritz vectors.",
              "Record file ELCN-THE."]),
    "EX12": ("Pounding of two planar frames (gap links), NLTH",
             "SAP2000; Table 12-1 (roof displacement, C1 axial force)",
             ["Gap link stiffness K and initial opening DI (Figure 12-1 "
              "shows the symbols only).",
              "Spacing between column lines C3 and C4 (the gap span).",
              "Story masses and the gravity loading behind the C1 axial "
              "force; damping; Ritz / modal count.",
              "Record file ELCN-THU.",
              "Section properties are \"user defined\" (W-shape weights "
              "from Figure 12-1 could be approximated with AISC values, "
              "as for Example 7)."]),
    "EX13": ("Base-isolated 2-story 3D frame (ISOLATOR1), NLTH",
             "SAP2000; Table 13-1 (C9 displacement, C1 axial force)",
             ["All ISOLATOR1 properties (K1, K2/K3, FY2/FY3, post-yield "
              "ratios, linear KE / DE).",
              "Column and beam section sizes (only E = 3000 ksi and 150 "
              "pcf are given).",
              "Loma Prieta records LP-TH0 / LP-TH90 (station, scale).",
              "Damping, Ritz count, duration; which corner is removed at "
              "the 2nd level is not stated."]),
    "EX14": ("Friction-pendulum base-isolated 3D frame (ISOLATOR2), NLTH",
             "SAP2000; Table 14-1 (C9 Uy displacement 4.2069 in, C1 axial "
             "force 38.25 kip)",
             ["Column and beam section sizes (only E = 3000 ksi, 150 pcf "
              "and the 8 in slabs are given).",
              "Loma Prieta records LP-TH0 / LP-TH90.",
              "Damping, Ritz count and duration; linear KE2/KE3, DE2/DE3 "
              "of the isolator.",
              "Available: the full ISOLATOR2 nonlinear data (K1 1E3, K2/K3 "
              "1E2, mu 0.04 / 0.03, rate 20, R 60) and the plan "
              "geometry."]),
}


def run_id(case_id: str) -> CaseResult:
    title, ref, items = BLOCKED[case_id]
    return CaseResult(case_id, title, TIER, source=ref, status="blocked",
                      tests="needs the ETABS installation files "
                            "(Example .EDB models and El Centro / Loma "
                            "Prieta records)",
                      blocked=list(items))
