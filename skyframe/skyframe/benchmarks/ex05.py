"""CSI ETABS Verification, Analysis Example 5 -- Three-story 3D braced
frame (L-plan), dynamic response spectrum analysis (Rev. 0).  TIER 2: the
two periods of Table 5-1 are rebuilt; the three member axial forces are
BLOCKED (El Centro spectrum ordinates not in the PDF).

Inputs (published, kip-in-s): four identical planar braced frames, three
column lines at 20 ft, 3 stories of 12 ft, X-braces in every bay and
story, all members axial-only (no bending stiffness), E = 29 500 ksi, A =
6 in^2; story mass 1.242 kip-s^2/in and mass moment of inertia 174 907.4
kip-s^2-in at the centre of mass (33'-4", 33'-4"); rigid diaphragm.
References: Wilson & Habibullah (1992) SAP90; Peterson (1981).

[derived] Frame lines read from the plan of Figure 5-1 (origin at X1/Y1,
plan 80 ft x 80 ft L): X1 (Y = 0) columns at X = 20, 40, 60 ft; X2 (Y =
80 ft) at X = 0, 20, 40 ft; Y1 (X = 0) at Y = 20, 40, 60 ft; Y2 (X = 80
ft) at Y = 0, 20, 40 ft.  Under a rigid diaphragm only each frame's line
coordinate matters; an independent condensed truss model with these
lines gives T1 = 0.326856, T2 = 0.320609 s (= ETABS to 5 digits).
[assumed] Pinned column bases; axial-only members = moment releases at
both ends (SkyFrame ``Mi, Mj``), torsion constant ~0; RX / RY set
inactive (pinned truss joints carry no rotation stiffness; ETABS
restrains such DOFs automatically).  The CM joint is created with a
massless in-plane stub (``_core.cm_stub``).
"""

from __future__ import annotations

from ._core import (CaseResult, PINNED, cm_stub, engine, mass, new_model,
                    section, support, timed)
from ._svg import Sketch

ID = "EX05"
TITLE = "Three-story 3D braced frame (L-plan), periods"
TIER = "2"

FT = 12.0
CM = (400.0, 400.0)          # 33'-4" = 400 in
REF = [("Period, mode 1", "0.32686", "0.32689"),
       ("Period, mode 2", "0.32061", "0.32064")]
BLOCKED = [
    "Table 5-1 axial forces (column C1 story 1: 279.47, brace D1: 194.51, "
    "brace D3: 120.53 kip, W&H): need the El Centro 1940 N-S spectrum "
    "ordinates (ELCN-RS1, ETABS install); the PDF also does not state the "
    "modal combination, the number of modes or which frame is reported."]

FRAMES = {   # name: (fixed axis, coordinate, column positions along it) [in]
    "X1": ("y", 0.0, (240.0, 480.0, 720.0)),
    "X2": ("y", 960.0, (0.0, 240.0, 480.0)),
    "Y1": ("x", 0.0, (240.0, 480.0, 720.0)),
    "Y2": ("x", 960.0, (0.0, 240.0, 480.0)),
}


def _pt(axis, c, s, z):
    return (s, c, z) if axis == "y" else (c, s, z)


def build():
    m = new_model("CSI Ex05", 29500.0, heights=[144.0] * 3, diaphragm=True)
    m.active_dof = ["UX", "UY", "UZ", "RZ"]
    section(m, "A6", 6.0, 1.0, 1.0, 1e-6)
    section(m, "STUB", 1e3, 1e6, 1e6, 1e6)
    for fname, (ax, c, cols) in FRAMES.items():
        for k in range(3):
            z0, z1 = 144.0 * k, 144.0 * (k + 1)
            st = f"Story{k + 1}"
            for i, s in enumerate(cols):
                m.add_member("column", "A6", _pt(ax, c, s, z0),
                             _pt(ax, c, s, z1), story=st,
                             uid=f"{fname}_C{i + 1}_{k + 1}",
                             releases="Mi,Mj")
            for b in range(2):
                s0, s1 = cols[b], cols[b + 1]
                m.add_member("brace", "A6", _pt(ax, c, s0, z0),
                             _pt(ax, c, s1, z1), story=st,
                             uid=f"{fname}_D{2 * b + 1}_{k + 1}",
                             releases="Mi,Mj")
                m.add_member("brace", "A6", _pt(ax, c, s0, z1),
                             _pt(ax, c, s1, z0), story=st,
                             uid=f"{fname}_D{2 * b + 2}_{k + 1}",
                             releases="Mi,Mj")
                m.add_member("beam", "A6", _pt(ax, c, s0, z1),
                             _pt(ax, c, s1, z1), story=st,
                             uid=f"{fname}_B{b + 1}_{k + 1}",
                             releases="Mi,Mj")
        for s in cols:
            support(m, _pt(ax, c, s, 0.0), PINNED)
    for k in range(3):
        z = 144.0 * (k + 1)
        cm_stub(m, "STUB", (240.0, 0.0, z), (CM[0], CM[1], z),
                uid=f"CMSTUB{k + 1}")
        mass(m, (CM[0], CM[1], z), mx=1.242, my=1.242, mrz=174907.4)
    m.num_modes = 4
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER, status="partial",
        source="Wilson & Habibullah (1992) SAP90; Peterson (1981); CSI "
               "ETABS Verification Example 5, Table 5-1",
        tests="3D model from planar truss frames, axial-only members, "
              "rigid diaphragm with eccentric CM + MMI, modal (RS blocked)")
    res.inputs = [("Units", "kip, in, s"), ("Members", "A = 6 in2, E = "
                                                       "29,500 ksi, axial "
                                                       "only"),
                  ("Frames", "4 planar X-braced frames, 2 bays x 20 ft, 3 "
                             "stories x 12 ft"),
                  ("Mass", "1.242 kip-s2/in + 174,907.4 kip-s2-in at CM "
                           "(33'-4\", 33'-4\")")]
    res.assumptions = [
        "[derived] Frame line positions read from the plan (only the line "
        "coordinate matters under a rigid diaphragm).",
        "[assumed] Pinned bases; axial-only = end moment releases; RX/RY "
        "inactive.",
        "CM joint created by a massless in-plane stub.",
    ]
    res.blocked = list(BLOCKED)
    md = engine(build()).run_modal(4)
    for k, (q, et, ref) in enumerate(REF):
        res.add(q, "", "s", ref, "Wilson & Habibullah 1992", et,
                float(md.periods[k]))
    sk = Sketch()
    for fname, (ax, c, cols) in FRAMES.items():
        pts = [(_pt(ax, c, s, 0)[0], _pt(ax, c, s, 0)[1]) for s in cols]
        sk.line(pts[0], pts[-1], "b")
        for p in pts:
            sk.dot(p, "col")
        sk.text((pts[1][0] + (30 if ax == "x" else 0),
                 pts[1][1] + (20 if ax == "y" else 0)), fname)
    for a, b in (((0, 0), (960, 0)), ((960, 0), (960, 480)),
                 ((960, 480), (480, 480)), ((480, 480), (480, 960)),
                 ((480, 960), (0, 960)), ((0, 960), (0, 0))):
        sk.line(a, b, "ol")
    sk.dot(CM, "cm").text((CM[0] + 20, CM[1]), "CM", "lbl", "start")
    res.svg = sk.render("Example 5 plan")
    return res
