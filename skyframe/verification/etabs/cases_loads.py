"""Frame additions F9-F10 and the load group L1-L5 (kip, ft).

Same definition format as cases.py, plus (see the cases.py docstring):

    stationSpacing   output stations along every frame at most this far apart (ft), both programs
    frames[].cardinal  insertion point (ETABS cardinal point, 10 = centroid), stiffness transformed
    frameLoads   [{pattern, frame, type, ...}]  type "point": P at relative distance a;
                 "trap": w1 at a to w2 at b (k/ft); gravity (down) positive
    tempLoads    [{pattern, frame, dT}]         uniform temperature change, deg F
    materials[].alpha                           thermal coefficient, 1/deg F
    patterns[].selfWeight                       self-weight multiplier of the pattern
    areaSections [{name, material, thickness, kind "slab"|"wall", shell "membrane"|"thin"}]
    areas        [{id, section, corners[4], mesh [n1, n2]?, toFrame "two"|"one"?}]
                 corners counter-clockwise seen from above (local 1 = corner 0 -> 1);
                 toFrame: membrane slab whose load goes to its edge beams, two-way or
                 one-way (one-way spans along local 1)
    areaLoads    [{pattern, area, q}]           uniform load, ksf, gravity (down) positive
"""

from __future__ import annotations

import math

from defs import STEEL_E, STEEL_NU, _COMMON, _b, _beam_section, _f, _j, _mat

CONC_E = 4000.0 * 144.0            # ksf (4,000 ksi)
CONC_NU = 0.2
CONC_W = 0.150                     # kcf


def _conc(weight=0.0):
    return [{"name": "CONC", "E": CONC_E, "nu": CONC_NU, "unitWeight": weight}]


def _pins(points):
    """Supports fixed in translation and rotation: members framing in carry their own releases."""
    return [{"at": p, "restraint": [1] * 6} for p in points]


def F9() -> dict:
    """A beam hung from its top face (cardinal point 8) between two pins: the offset makes it arch."""
    E, A, I, d = STEEL_E, 0.1, 0.03, 1.5
    L, w = 30.0, 2.0
    e, r2 = d / 2, I / A
    H = e * w * L**2 / (12 * (e * e + r2))         # tension from compatibility at the pins
    mid = [L / 2, 0.0, 12.0]
    return {
        "id": "F9", "title": "Insertion point: beam hung from its top face",
        "purpose": ("Cardinal point 8 (top center) with the stiffness transformed for the offset: the beam axis "
                    "sits d/2 below the joints, so pins at both ends develop an axial force and relieve the "
                    "span moment. A wrong offset direction or an untransformed stiffness shows at once."),
        "units": "kip-ft",
        "settings": dict(_COMMON, insertionPoint="cardinal point 8, stiffness transformed for the offset",
                         supports="pinned both ends (no slip)"),
        "materials": _mat(),
        "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": mid, "section": "BM", "angle": 0.0, "cardinal": 8},
                   {"id": "B2", "i": mid, "j": [L, 0.0, 12.0], "section": "BM", "angle": 0.0, "cardinal": 8}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1, 1, 1, 1, 0, 1]},
                     {"at": [L, 0.0, 12.0], "restraint": [1, 1, 1, 1, 0, 1]}],
        "patterns": [{"name": "W"}],
        "distLoads": [{"pattern": "W", "frame": "B1", "w": w}, {"pattern": "W", "frame": "B2", "w": w}],
        "checks": [
            _f("W-P", "Axial tension in the beam", "W", "B1", "i", "P", H,
               "H = e w L^2 / 12(e^2 + I/A), e = d/2", how="signed"),
            _f("W-Mmid", "Midspan moment", "W", "B1", "j", "M3", w * L**2 / 8 - H * e, "wL^2/8 - H e"),
            _j("W-UZ", "Midspan deflection", "W", mid, "UZ",
               -(5 * w * L**4 / (384 * E * I) - H * e * L**2 / (8 * E * I)), "-(5wL^4/384EI - H e L^2/8EI)"),
            _b("W-FZ", "Total vertical reaction", "W", "FZ", w * L, "wL"),
            dict(_b("W-FX", "Horizontal reactions cancel", "W", "FX", 0.0, "0"), scale=H),
        ],
    }


def F10() -> dict:
    """A deep cantilever with shear deformation on (As2 = 5/6 A)."""
    E, nu = CONC_E, CONC_NU
    G = E / (2 * (1 + nu))
    b, h, L, P = 1.0, 3.0, 6.0, 100.0
    A, I, As2 = b * h, b * h**3 / 12, 5 / 6 * b * h
    tip = [L, 0.0, 12.0]
    sec = {"name": "DEEP", "material": "CONC", "A": A, "I33": I, "I22": h * b**3 / 12, "J": 0.263 * h * b**3,
           "As2": As2, "As3": 5 / 6 * A, "depth": h, "width": b}
    return {
        "id": "F10", "title": "Shear deformation in a deep beam",
        "purpose": ("A 6 ft cantilever 3 ft deep, shear areas on: the shear term PL/GAs is 18% of the tip "
                    "deflection, so a program ignoring As2 is far off."),
        "units": "kip-ft",
        "settings": dict(_COMMON, shearDeformation="on (As2 = As3 = 5/6 A)", supports="fixed at x = 0"),
        "materials": _conc(),
        "sections": [sec],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": tip, "section": "DEEP", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1] * 6}],
        "patterns": [{"name": "P"}],
        "pointLoads": [{"pattern": "P", "at": tip, "f": [0, 0, -P, 0, 0, 0]}],
        "checks": [
            _j("P-UZ", "Tip deflection, bending plus shear", "P", tip, "UZ",
               -(P * L**3 / (3 * E * I) + P * L / (G * As2)), "-(PL^3/3EI + PL/G As2)"),
            _j("P-RY", "Tip rotation (bending only)", "P", tip, "RY", P * L**2 / (2 * E * I), "PL^2 / 2EI",
               how="abs"),
            _f("P-M3", "Moment at the support", "P", "B1", "i", "M3", P * L, "PL"),
            _f("P-V2", "Shear", "P", "B1", "i", "V2", P, "P"),
        ],
    }


# ── slabs on beams ──────────────────────────────────────────────────────────
def _bay(Lx, Ly, z=12.0, split=True):
    """Four edge beams round a Lx x Ly bay, simply supported at the corners (moment releases at
    the corners; torsion released at one end).  Each beam is split at midspan when ``split``."""
    c = [[0.0, 0.0, z], [Lx, 0.0, z], [Lx, Ly, z], [0.0, Ly, z]]
    frames = []
    for k, (p, q) in enumerate(zip(c, c[1:] + c[:1])):
        bid = ["BS", "BE", "BN", "BW"][k]
        m = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, z]
        rel_i = {"M2": 0.0, "M3": 0.0}
        rel_j = {"M2": 0.0, "M3": 0.0, "T": 0.0}
        if split:
            frames += [{"id": bid + "1", "i": p, "j": m, "section": "BM", "angle": 0.0, "releases": {"i": rel_i}},
                       {"id": bid + "2", "i": m, "j": q, "section": "BM", "angle": 0.0, "releases": {"j": rel_j}}]
        else:
            frames.append({"id": bid, "i": p, "j": q, "section": "BM", "angle": 0.0,
                           "releases": {"i": rel_i, "j": rel_j}})
    return c, frames


def L1() -> dict:
    """Two-way slab load to the four edge beams of a square bay (45-degree tributary areas)."""
    E, I = STEEL_E, 0.03
    L, q = 20.0, 0.1
    c, frames = _bay(L, L)
    w0 = q * L / 2                                   # triangle peak on each beam
    mid_s = [L / 2, 0.0, 12.0]
    return {
        "id": "L1", "title": "Two-way slab load to beams",
        "purpose": ("A membrane slab on a square bay passes its load to the four edge beams by 45-degree "
                    "tributary areas: each beam carries a triangle peaking at qL/2."),
        "units": "kip-ft",
        "settings": dict(_COMMON, slab="membrane, load to frames two-way", supports="corners; beams simply supported"),
        "materials": _mat(),
        "sections": [_beam_section()],
        "areaSections": [{"name": "DECK", "material": "STEEL", "thickness": 0.5, "kind": "slab", "shell": "membrane"}],
        "stories": [{"name": "Story1", "height": 12.0}],
        "stationSpacing": 0.5,
        "frames": frames,
        "areas": [{"id": "S1", "section": "DECK", "corners": c, "toFrame": "two"}],
        "supports": _pins(c),
        "patterns": [{"name": "Q"}],
        "areaLoads": [{"pattern": "Q", "area": "S1", "q": q}],
        "checks": [
            _b("Q-FZ", "Total vertical reaction", "Q", "FZ", q * L * L, "q L^2"),
            _f("Q-V", "Beam end shear", "Q", "BS1", "i", "V2", q * L * L / 8, "q L^2 / 8"),
            _f("Q-Vw", "Beam end shear, perpendicular beam", "Q", "BW1", "i", "V2", q * L * L / 8, "q L^2 / 8"),
            _f("Q-M", "Beam midspan moment", "Q", "BS1", "j", "M3", w0 * L**2 / 12, "w0 L^2 / 12 = q L^3 / 24"),
            _j("Q-UZ", "Beam midspan deflection", "Q", mid_s, "UZ", -w0 * L**4 / (120 * E * I), "-w0 L^4 / 120EI"),
        ],
    }


KNOWN_ONE_WAY = ("ETABS's one-way (ribbed membrane) slab sends the load within a quarter span, 2.5 ft, of each "
                 "short beam to that beam (1.25 kip per end), so the long beams get 6.25 kip instead of 7.5; "
                 "SkyFrame follows the textbook one-way rule")


def L1S() -> dict:
    """The L1 bay with the slab as a meshed thin shell connected to the beams at every slab node."""
    L, q, n, t = 20.0, 0.1, 8, 0.5
    z = 12.0
    c = [[0.0, 0.0, z], [L, 0.0, z], [L, L, z], [0.0, L, z]]
    frames = []
    for k, (p, r) in enumerate(zip(c, c[1:] + c[:1])):
        bid = ["BS", "BE", "BN", "BW"][k]
        pts = [[p[0] + (r[0] - p[0]) * i / n, p[1] + (r[1] - p[1]) * i / n, z] for i in range(n + 1)]
        for i in range(n):
            f = {"id": f"{bid}{i + 1}", "i": pts[i], "j": pts[i + 1], "section": "BM", "angle": 0.0}
            if i == 0:
                f["releases"] = {"i": {"M2": 0.0, "M3": 0.0}}
            if i == n - 1:
                f["releases"] = {"j": {"M2": 0.0, "M3": 0.0, "T": 0.0}}
            frames.append(f)
    mid = [L / 2, L / 2, z]
    return {
        "id": "L1S", "title": "Two-way slab as a shell on beams",
        "purpose": ("The L1 bay with the slab modelled as a meshed thin shell (8 x 8, 2.5 ft) sharing every node "
                    "with its edge beams: slab and beams bend together and share the load by stiffness, as in a "
                    "real floor. The total is exact; the split between slab and beams is compared."),
        "units": "kip-ft",
        "settings": dict(_COMMON, slab="6 in concrete, Shell-Thin, 8 x 8 mesh; beams split at every slab node",
                         supports="corners; beams simply supported at the corners"),
        "materials": _mat() + _conc(),
        "sections": [_beam_section()],
        "areaSections": [{"name": "SLAB", "material": "CONC", "thickness": t, "kind": "slab", "shell": "thin"}],
        "stories": [{"name": "Story1", "height": z}],
        "stationSpacing": 0.5,
        "frames": frames,
        "areas": [{"id": "S1", "section": "SLAB", "corners": c, "mesh": [n, n]}],
        "supports": _pins(c),
        "patterns": [{"name": "Q"}],
        "areaLoads": [{"pattern": "Q", "area": "S1", "q": q}],
        "checks": [
            _b("Q-FZ", "Total vertical reaction", "Q", "FZ", q * L * L, "q L^2"),
            _j("Q-UZc", "Slab centre deflection", "Q", mid, "UZ", None, "programs compared"),
            _j("Q-UZb", "Beam midspan deflection", "Q", [L / 2, 0.0, z], "UZ", None, "programs compared"),
            _f("Q-V", "Beam end shear", "Q", "BS1", "i", "V2", None, "programs compared"),
            _f("Q-M", "Beam midspan moment", "Q", "BS4", "j", "M3", None, "programs compared"),
        ],
    }


def L2() -> dict:
    """One-way slab: a 10 x 30 ft bay spanning 10 ft onto the two long beams."""
    E, I = STEEL_E, 0.03
    Lx, Ly, q = 10.0, 30.0, 0.1
    c, frames = _bay(Lx, Ly)
    w = q * Lx / 2
    return {
        "id": "L2", "title": "One-way slab load to beams",
        "purpose": ("A one-way membrane slab spanning along X: the two long beams each take a uniform qLx/2 "
                    "and the short beams take nothing."),
        "units": "kip-ft",
        "settings": dict(_COMMON, slab="membrane, load to frames one-way along local 1 (X)",
                         supports="corners; beams simply supported"),
        "materials": _mat(),
        "sections": [_beam_section()],
        "areaSections": [{"name": "DECK", "material": "STEEL", "thickness": 0.5, "kind": "slab", "shell": "membrane"}],
        "stories": [{"name": "Story1", "height": 12.0}],
        "stationSpacing": 0.5,
        "frames": frames,
        "areas": [{"id": "S1", "section": "DECK", "corners": c, "toFrame": "one"}],
        "supports": _pins(c),
        "patterns": [{"name": "Q"}],
        "areaLoads": [{"pattern": "Q", "area": "S1", "q": q}],
        "checks": [
            _b("Q-FZ", "Total vertical reaction", "Q", "FZ", q * Lx * Ly, "q Lx Ly"),
            dict(_f("Q-Vlong", "Long beam end shear", "Q", "BE1", "i", "V2", w * Ly / 2, "(q Lx / 2) Ly / 2"),
                 knownDifference=KNOWN_ONE_WAY),
            dict(_f("Q-Mlong", "Long beam midspan moment", "Q", "BE1", "j", "M3", w * Ly**2 / 8,
                    "(q Lx / 2) Ly^2 / 8"), knownDifference=KNOWN_ONE_WAY),
            dict(_j("Q-UZ", "Long beam midspan deflection", "Q", [Lx, Ly / 2, 12.0], "UZ",
                    -5 * w * Ly**4 / (384 * E * I), "-5 w Ly^4 / 384EI"), knownDifference=KNOWN_ONE_WAY),
            dict(_f("Q-Vshort", "Short beam carries nothing", "Q", "BS1", "i", "V2", 0.0, "0"), scale=w * Ly / 2,
                 knownDifference=KNOWN_ONE_WAY),
        ],
    }


def L3() -> dict:
    """Self-weight of columns, beams and a slab on a one-bay frame."""
    L, h, t = 20.0, 12.0, 0.5
    col = {"name": "COL", "material": "CONC", "A": 1.0, "I33": 1 / 12, "I22": 1 / 12, "J": 0.1406,
           "As2": 0.0, "As3": 0.0, "depth": 1.0, "width": 1.0}
    bm = {"name": "BM", "material": "CONC", "A": 2.0, "I33": 2.0**3 / 12, "I22": 2.0 / 12, "J": 0.458,
          "As2": 0.0, "As3": 0.0, "depth": 2.0, "width": 1.0}
    c = [[0.0, 0.0, h], [L, 0.0, h], [L, L, h], [0.0, L, h]]
    frames = [{"id": f"C{k + 1}", "i": [p[0], p[1], 0.0], "j": p, "section": "COL", "angle": 0.0}
              for k, p in enumerate(c)]
    frames += [{"id": f"B{k + 1}", "i": p, "j": q, "section": "BM", "angle": 0.0}
               for k, (p, q) in enumerate(zip(c, c[1:] + c[:1]))]
    Wc, Wb, Ws = 4 * 1.0 * h * CONC_W, 4 * 2.0 * L * CONC_W, L * L * t * CONC_W
    W = Wc + Wb + Ws
    return {
        "id": "L3", "title": "Self-weight",
        "purpose": ("Self-weight from the material unit weight (150 pcf) on columns, beams and a slab: the "
                    "total and, by symmetry, each column's share are exact."),
        "units": "kip-ft",
        "settings": dict(_COMMON, selfWeight="on: unit weight 0.150 kcf, multiplier 1", slab="membrane, two-way",
                         supports="fixed bases"),
        "materials": _conc(CONC_W),
        "sections": [col, bm],
        "areaSections": [{"name": "SLAB", "material": "CONC", "thickness": t, "kind": "slab", "shell": "membrane"}],
        "stories": [{"name": "Story1", "height": h}],
        "frames": frames,
        "areas": [{"id": "S1", "section": "SLAB", "corners": c, "toFrame": "two"}],
        "supports": [{"at": [p[0], p[1], 0.0], "restraint": [1] * 6} for p in c],
        "patterns": [{"name": "SW", "selfWeight": 1.0}],
        "checks": [
            _b("SW-FZ", "Total weight", "SW", "FZ", W,
               "0.15 (4 x 1 x 12 + 4 x 2 x 20 + 20 x 20 x 0.5) = 61.2 kip"),
            _f("SW-P", "Column axial at the base, a quarter by symmetry", "SW", "C1", "i", "P", -W / 4, "-W / 4",
               how="signed"),
            _b("SW-MX", "Moment about X at the origin (load centred)", "SW", "MX", W * L / 2, "W L / 2"),
        ],
    }


def L4() -> dict:
    """Point, triangular and partial uniform loads along a simply supported beam."""
    E, I = STEEL_E, 0.03
    L, P, a, w0, w = 30.0, 10.0, 10.0, 3.0, 2.0
    b = L - a
    Rtri = w0 * L / 6
    return {
        "id": "L4", "title": "Point, triangular and partial loads on a beam",
        "purpose": ("Frame loads other than a full uniform load: a point load at a third point, a triangle "
                    "rising to 3 k/ft and a uniform load over the middle half."),
        "units": "kip-ft",
        "settings": dict(_COMMON, supports="pin and roller"),
        "materials": _mat(),
        "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "stationSpacing": 0.5,
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": [L, 0.0, 12.0], "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1, 1, 1, 1, 0, 0]},
                     {"at": [L, 0.0, 12.0], "restraint": [0, 1, 1, 1, 0, 0]}],
        "patterns": [{"name": "PT"}, {"name": "TRI"}, {"name": "PART"}],
        "frameLoads": [{"pattern": "PT", "frame": "B1", "type": "point", "a": a / L, "P": P},
                       {"pattern": "TRI", "frame": "B1", "type": "trap", "a": 0.0, "b": 1.0, "w1": 0.0, "w2": w0},
                       {"pattern": "PART", "frame": "B1", "type": "trap", "a": 0.25, "b": 0.75, "w1": w, "w2": w}],
        "checks": [
            _f("PT-V", "Point load: shear at the near support", "PT", "B1", "i", "V2", P * b / L, "P b / L"),
            _f("PT-M", "Point load: moment under the load", "PT", "B1", a, "M3", P * a * b / L, "P a b / L"),
            _j("PT-RY", "Point load: slope at the near support", "PT", [0.0, 0.0, 12.0], "RY",
               P * a * b * (L + b) / (6 * E * I * L), "P a b (L + b) / 6EIL", how="abs"),
            _f("TRI-Vi", "Triangle: shear at the low end", "TRI", "B1", "i", "V2", Rtri, "w0 L / 6"),
            _f("TRI-Vj", "Triangle: shear at the high end", "TRI", "B1", "j", "V2", w0 * L / 3, "w0 L / 3"),
            _f("TRI-M", "Triangle: moment at midspan", "TRI", "B1", L / 2, "M3",
               Rtri * L / 2 - w0 * (L / 2)**3 / (6 * L), "R x - w0 x^3 / 6L"),
            _f("PART-M", "Middle-half load: moment at midspan", "PART", "B1", L / 2, "M3",
               w * L / 4 * L / 2 - w * (L / 4)**2 / 2, "R L/2 - w (L/4)^2 / 2"),
            _b("PART-FZ", "Middle-half load: total reaction", "PART", "FZ", w * L / 2, "w L / 2"),
        ],
    }


def L5() -> dict:
    """Uniform temperature rise: a bar fixed at both ends, and a bar free at one end."""
    E, A, alpha, dT, L = STEEL_E, 0.1, 6.5e-6, 50.0, 30.0
    mat = [{"name": "STEEL", "E": E, "nu": STEEL_NU, "unitWeight": 0.0, "alpha": alpha}]
    return {
        "id": "L5", "title": "Temperature load",
        "purpose": ("A uniform 50 F rise: fully restrained it builds axial compression E A alpha dT; free at "
                    "one end it lengthens alpha dT L with no force."),
        "units": "kip-ft",
        "settings": dict(_COMMON, temperature="uniform +50 F, alpha 6.5e-6 /F", supports="fixed, or fixed-free"),
        "materials": mat,
        "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": [L, 0.0, 12.0], "section": "BM", "angle": 0.0},
                   {"id": "B2", "i": [0.0, 10.0, 12.0], "j": [L, 10.0, 12.0], "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1] * 6}, {"at": [L, 0.0, 12.0], "restraint": [1] * 6},
                     {"at": [0.0, 10.0, 12.0], "restraint": [1] * 6}],
        "patterns": [{"name": "T"}],
        "tempLoads": [{"pattern": "T", "frame": "B1", "dT": dT}, {"pattern": "T", "frame": "B2", "dT": dT}],
        "checks": [
            _f("T-P", "Restrained bar: axial compression", "T", "B1", "i", "P", -E * A * alpha * dT,
               "-E A alpha dT", how="signed"),
            _j("T-UX", "Free bar: end moves", "T", [L, 10.0, 12.0], "UX", alpha * dT * L, "alpha dT L"),
            dict(_f("T-P0", "Free bar: no force", "T", "B2", "i", "P", 0.0, "0"), scale=E * A * alpha * dT),
            dict(_b("T-FX", "Reactions cancel", "T", "FX", 0.0, "0"), scale=E * A * alpha * dT),
        ],
    }


CASES = {"F9": F9, "F10": F10, "L1": L1, "L1S": L1S, "L2": L2, "L3": L3, "L4": L4, "L5": L5}
