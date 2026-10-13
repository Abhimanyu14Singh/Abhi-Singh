"""Gap case (not in CSI's ETABS analysis set): floor-diaphragm design
forces Fpx (ASCE 7-16 / 7-22 §12.10.1.1, Eqs. 12.10-1 .. 12.10-3).

Four stories of 4 m on a one-bay 6 m x 6 m frame with rigid diaphragms;
story seismic weights w = 1200, 1000, 1000, 800 kN (bottom -> top);
equivalent lateral forces from a seismic coefficient Cs = 1/6 (V = Cs W
= 666.67 kN) distributed with k = 1 (F_x = V w_x h_x / sum w h); SDS =
1.0, Ie = 1.0.  Hand calculation per level x:

    Fpx = (sum_{i>=x} F_i / sum_{i>=x} w_i) w_px,
    0.2 SDS Ie w_px <= Fpx <= 0.4 SDS Ie w_px      (w_px = w_x)

-> 226.95, 244.29, 207.70 kN for levels 4, 3, 2 and the 0.2 SDS Ie w_px
minimum, 240 kN, at level 1 (the equation gives 200).  Each level's
SkyFrame Fpx pattern is then applied alone and the engine's base shear
must equal that Fpx (the diaphragm force reaches the base).
"""

from __future__ import annotations

from skyframe.core import autolateral as al
from skyframe.core import fpx as _fpx

from ._core import (CaseResult, FIXED, engine, new_model, section, sig,
                    support, timed)
from ._svg import elevation

ID = "GAP-FPX"
TITLE = "Floor-diaphragm design forces Fpx (ASCE 7 12.10)"
TIER = "gap"

W = [1200.0, 1000.0, 1000.0, 800.0]
HS = [4.0, 4.0, 4.0, 4.0]
CS, SDS, IE = 1.0 / 6.0, 1.0, 1.0


def hand():
    z = [4.0 * (i + 1) for i in range(4)]
    V = CS * sum(W)
    swh = sum(w * h for w, h in zip(W, z))
    F = [V * w * h / swh for w, h in zip(W, z)]
    out = []
    for x in range(4):
        eq = sum(F[x:]) / sum(W[x:]) * W[x]
        out.append(min(max(eq, 0.2 * SDS * IE * W[x]), 0.4 * SDS * IE * W[x]))
    return F, out


def build():
    m = new_model("fpx", 25.0e6, nu=0.2, heights=HS, diaphragm=True)
    section(m, "C", 0.25, 0.005, 0.005, 0.008)
    section(m, "B", 0.18, 0.005, 0.002, 0.004)
    pts = [(0, 0), (6, 0), (6, 6), (0, 6)]
    for k in range(4):
        z0, z1 = 4.0 * k, 4.0 * (k + 1)
        st = m.stories[k].name
        for i, (x, y) in enumerate(pts):
            m.add_member("column", "C", (x, y, z0), (x, y, z1), story=st,
                         uid=f"C{i}_{k}")
            x2, y2 = pts[(i + 1) % 4]
            m.add_member("beam", "B", (x, y, z1), (x2, y2, z1), story=st,
                         uid=f"B{i}_{k}")
    for x, y in pts:
        support(m, (x, y, 0), FIXED)
    weights = {s.name: w for s, w in zip(m.stories, W)}
    al.generate(m, "user_coefficient", "EQX", C=CS, k=1.0, weights=weights)
    return m, weights


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="ASCE 7-16/7-22 §12.10.1.1 Eqs. 12.10-1..3 (hand "
               "calculation)",
        tests="Diaphragm design forces (ETABS Diaphragm Design Forces "
              "table) and their application as load patterns")
    res.inputs = [("Stories", "4 x 4 m, rigid diaphragms"),
                  ("Weights", "1200 / 1000 / 1000 / 800 kN"),
                  ("ELF", "Cs = 1/6, k = 1"),
                  ("Fpx", "SDS = 1.0, Ie = 1.0, w_px = w_x")]
    res.assumptions = ["Story forces from SkyFrame's user-coefficient "
                       "generator (V = Cs W, k = 1) with explicit weights."]
    F, ref = hand()
    m, weights = build()
    rep = _fpx.compute(m, "EQX", SDS, IE, weights=weights)
    by = {r["story"]: r for r in rep["stories"]}
    names = _fpx.generate_patterns(m, "EQX", SDS, IE, weights=weights)
    for n in names:
        m.add_case(n, {n: 1.0})
    eng = engine(m)
    for k, s in enumerate(m.stories):
        row = by[s.name]
        res.add(f"Fpx ({row['governs']})", s.name, "kN", sig(ref[k]),
                "ASCE 7 Eq. 12.10-1..3", None, float(row["Fpx"]),
                group="Fpx table")
    for k, s in enumerate(m.stories):
        r = eng.run_static(f"EQX_Fpx_{s.name}")
        res.add("Base shear under the Fpx pattern", s.name, "kN",
                sig(ref[k]), "ASCE 7 Eq. 12.10-1..3 (statics)", None,
                abs(float(r.base["FX"])), group="Fpx applied")
    sk = elevation([0, 6], [4, 8, 12, 16])
    res.svg = sk.render("Fpx frame")
    return res
