"""CSI ETABS Verification, Analysis Example 1 -- Plane frame with beam span
loads, static gravity load analysis (Rev. 0).

Inputs (published, kip-in-s): one story 10 ft (120 in), column lines at
X = 0, 18 ft, 36 ft (0, 216, 432 in); E = 3000 ksi; columns 12" x 24",
beams 12" x 30"; beam-to-column connection pinned on top of C1 and C3
(figure "Pinned Connection" dots); axial and shear deformations neglected,
no rigid offsets.  Case I: point loads 50/100/100/100/50 k at equal 9 ft
spaces (the three over C1, C2, C3 are joint loads); Case II: 10 k/ft on
both beams.  Reference: AISC (1989) propped-cantilever tables (Table 1-1,
beam B1, stations 0, 1/4, 1/2, 3/4, 1).

[derived] I = b h^3 / 12 from the printed dimensions: columns 13 824 in^4,
beams 27 000 in^4.
[assumed] "axial deformation neglected" = A x 1e6 (SkyFrame has no
rigid-axial switch; the residual is ~1e-8 relative); shear deformation is
off by default (no shear areas).  Column bases fixed (figure hatches);
the base condition does not affect beam B1 for this symmetric loading.
Moment releases at B1 end I and B2 end J reproduce the pinned
connections.  At the Case I mid-span point load the 1/2-point shear is
the value just right of the load (as ETABS reports).
"""

from __future__ import annotations

from skyframe.core.model import MemberLoad

from ._core import (CaseResult, FIXED, engine, new_model, section, support,
                    load, timed)
from ._svg import elevation

ID = "EX01"
TITLE = "Plane frame with beam span loads (static)"
TIER = "1"

BIG = 1.0e6     # "axial deformation neglected"

REF = {   # Table 1-1, beam B1: (quantity, location, ETABS, theoretical)
    "Case I": [
        ("Bending moment", "End I", "0.00", "0.00"),
        ("Bending moment", "1/4 point", "1,687.50", "1,687.50"),
        ("Bending moment", "1/2 point", "3,375.00", "3,375.00"),
        ("Bending moment", "3/4 point", "-337.50", "-337.50"),
        ("Bending moment", "End J", "-4,050.00", "-4,050.00"),
        ("Shear force", "End I", "-31.25", "-31.25"),
        ("Shear force", "1/4 point", "-31.25", "-31.25"),
        ("Shear force", "1/2 point", "68.75", "68.75"),
        ("Shear force", "3/4 point", "68.75", "68.75"),
        ("Shear force", "End J", "68.75", "68.75"),
    ],
    "Case II": [
        ("Bending moment", "End I", "0.00", "0.00"),
        ("Bending moment", "1/4 point", "2,430.00", "2,430.00"),
        ("Bending moment", "1/2 point", "2,430.00", "2,430.00"),
        ("Bending moment", "3/4 point", "0.00", "0.00"),
        ("Bending moment", "End J", "-4,860.00", "-4,860.00"),
        ("Shear force", "End I", "-67.50", "-67.50"),
        ("Shear force", "1/4 point", "-22.50", "-22.50"),
        ("Shear force", "1/2 point", "22.50", "22.50"),
        ("Shear force", "3/4 point", "67.50", "67.50"),
        ("Shear force", "End J", "112.50", "112.50"),
    ],
}
_LOC = {"End I": 0.0, "1/4 point": 0.25, "1/2 point": 0.5,
        "3/4 point": 0.75, "End J": 1.0}


def build():
    L, H = 216.0, 120.0
    m = new_model("CSI Ex01", 3000.0, nu=0.2, heights=[H])
    m.active_dof = ["UX", "UZ", "RY"]
    section(m, "COL", 12 * 24 * BIG, 12 * 24 ** 3 / 12.0)
    section(m, "BM", 12 * 30 * BIG, 12 * 30 ** 3 / 12.0)
    for i, x in enumerate((0.0, L, 2 * L)):
        m.add_member("column", "COL", (x, 0, 0), (x, 0, H), story="Story1",
                     uid=f"C{i + 1}")
        support(m, (x, 0, 0), FIXED)
    b1 = m.add_member("beam", "BM", (0, 0, H), (L, 0, H), story="Story1",
                      uid="B1", releases="Mi")
    b2 = m.add_member("beam", "BM", (L, 0, H), (2 * L, 0, H),
                      story="Story1", uid="B2", releases="Mj")
    for b in (b1, b2):
        b.output_stations = {"min_number": 5}    # 0, 1/4, 1/2, 3/4, 1
    for x, f in ((0.0, 50.0), (L, 100.0), (2 * L, 50.0)):
        load(m, "CASE1", (x, 0, H), fz=-f)
    p1 = m.pattern("CASE1")
    p1.member_loads += [MemberLoad("B1", "point", 100.0, a=0.5),
                        MemberLoad("B2", "point", 100.0, a=0.5)]
    p2 = m.pattern("CASE2")
    p2.member_loads += [MemberLoad("B1", "udl", 10.0 / 12.0),
                        MemberLoad("B2", "udl", 10.0 / 12.0)]
    m.add_case("CASE1", {"CASE1": 1.0})
    m.add_case("CASE2", {"CASE2": 1.0})
    return m


@timed
def run() -> CaseResult:
    res = CaseResult(
        ID, TITLE, TIER,
        source="AISC (1989) propped-cantilever tables (closed form); "
               "CSI ETABS Verification Example 1, Table 1-1",
        tests="2D frame statics, moment end releases, point + uniform span "
              "loads, station internal forces")
    res.inputs = [("Units", "kip, in, s"), ("E", "3000 ksi"),
                  ("Columns", "12 x 24 in: A = 288 in2, I = 13,824 in4"),
                  ("Beams", "12 x 30 in: A = 360 in2, I = 27,000 in4"),
                  ("Geometry", "2 bays x 18 ft, story 10 ft"),
                  ("Case I", "50/100/100/100/50 k at 9 ft spacing"),
                  ("Case II", "10 k/ft on B1 and B2")]
    res.assumptions = [
        "[derived] I = b h^3/12 from the printed member dimensions.",
        "[assumed] Axial deformation neglected: A multiplied by 1e6.",
        "Shear deformation off (SkyFrame default, no shear areas).",
        "Pinned connections = moment releases at B1 end I and B2 end J; "
        "all bases fixed (immaterial for B1 under symmetric loads).",
        "Live-load reduction off (never applied to analysis output).",
    ]
    m = build()
    eng = engine(m)
    for case, key in (("CASE1", "Case I"), ("CASE2", "Case II")):
        r = eng.run_static(case)
        st = r.member_stations["B1"]
        xs = st["x"]
        Lb = m.members[3].length
        for qty, loc, et, th in REF[key]:
            x = _LOC[loc] * Lb
            # last station at x (= value just right of a point load)
            k = max(i for i, xx in enumerate(xs) if abs(xx - x) < 1e-6)
            v = st["M3"][k] if qty.startswith("Bending") else st["V2"][k]
            res.add(qty, loc, "kip-in" if qty.startswith("Bending") else
                    "kip", th, "AISC propped cantilever", et, float(v),
                    cls="internal", group=key)
    sk = elevation([0, 216, 432], [120])
    sk.dot((0, 120), "pin").dot((432, 120), "pin")
    for x, f in ((0, "50k"), (108, "100k"), (216, "100k"), (324, "100k"),
                 (432, "50k")):
        sk.arrow((x, 128), (0, -1), f)
    for x, lab in ((108, "B1"), (324, "B2")):
        sk.text((x, 108), lab)
    res.svg = sk.render("Example 1 elevation")
    return res
