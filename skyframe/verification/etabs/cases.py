"""Verification test definitions: one function per model, kip and ft.

Each definition is the single source for both programs: ``run_etabs.py``
builds it in ETABS through the API and ``skyframe_side.py`` builds it in
SkyFrame.  Hand-calculated answers are worked out here, next to the model.

    python cases.py            # writes cases/<id>.json for every case

Definition keys (all lengths ft, forces kip, E/G ksf, A ft2, I/J ft4):

    id, title, purpose, units ("kip-ft")
    settings     the fairness settings fixed in both programs
    materials    [{name, E, nu, unitWeight}]
    sections     [{name, material, A, I33, I22, J, As2, As3, depth, width}]
                 As2 = As3 = 0: no shear deformation in either program
    stories      [{name, height}] bottom up
    frames       [{id, i, j, section, angle}]
    supports     [{at, restraint[6]}]
    patterns     [{name}]                each also a linear static case
    pointLoads   [{pattern, at, f[6]}]   global FX FY FZ MX MY MZ
    distLoads    [{pattern, frame, w}]   uniform load along the whole frame, k/ft, gravity (down) positive
    checks       [{id, label, case, quantity, at|frame|-, comp, hand, formula, how}]
                 quantity: "joint" (comp UX..RZ, signed), "base" (FX..MZ,
                 signed, about the global origin) or "frame" (P V2 V3 T M2
                 M3 at station "i"/"j"; how "abs" compares magnitudes)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent

STEEL_E = 29000.0 * 144.0          # ksf (29,000 ksi)
STEEL_NU = 0.3


def F1() -> dict:
    """Cantilever column, a separate tip load along X, Y, Z and a torque."""
    L, P, N, T = 12.0, 10.0, 50.0, 5.0
    A, I33, I22, J = 0.1, 0.02, 0.005, 0.0005       # I33 = 4 x I22: an axis swap cannot hide
    E, nu = STEEL_E, STEEL_NU
    G = E / (2 * (1 + nu))
    top = [0.0, 0.0, L]
    return {
        "id": "F1",
        "title": "Cantilever column, tip load both ways",
        "purpose": ("Axial, bending about both local axes and torsion of one column; "
                    "confirms both programs put local 2 along +X for a column at 0 degrees, "
                    "so the strong axis (I33) resists load along X."),
        "units": "kip-ft",
        "settings": {
            "frameAxes": "ETABS default (column local 2 = +X at angle 0)",
            "shearDeformation": "off (As2 = As3 = 0)",
            "selfWeight": "none (unit weight 0)",
            "mass": "none; modal case not run",
            "pDelta": "off",
            "rigidZones": "none",
            "endOffsets": "off (ETABS automatic end length offsets disabled, forces reported joint to joint)",
        },
        "materials": [{"name": "STEEL", "E": E, "nu": nu, "unitWeight": 0.0}],
        "sections": [{"name": "COL", "material": "STEEL", "A": A, "I33": I33, "I22": I22, "J": J,
                      "As2": 0.0, "As3": 0.0, "depth": 1.0, "width": 0.5}],
        "stories": [{"name": "Story1", "height": L}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": top, "section": "COL", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": [1, 1, 1, 1, 1, 1]}],
        "patterns": [{"name": "PX"}, {"name": "PY"}, {"name": "PZ"}, {"name": "TZ"}],
        "pointLoads": [
            {"pattern": "PX", "at": top, "f": [P, 0, 0, 0, 0, 0]},
            {"pattern": "PY", "at": top, "f": [0, P, 0, 0, 0, 0]},
            {"pattern": "PZ", "at": top, "f": [0, 0, -N, 0, 0, 0]},
            {"pattern": "TZ", "at": top, "f": [0, 0, 0, 0, 0, T]},
        ],
        "checks": [
            _j("PX-UX", "Top moves along X (strong axis)", "PX", top, "UX", P * L**3 / (3 * E * I33), "PL^3 / 3EI33"),
            _j("PX-RY", "Top rotates about Y", "PX", top, "RY", P * L**2 / (2 * E * I33), "PL^2 / 2EI33"),
            _b("PX-FX", "Base shear X", "PX", "FX", -P, "-P"),
            _b("PX-MY", "Base moment about Y", "PX", "MY", -P * L, "-PL"),
            _f("PX-M3", "Column moment at base, M3", "PX", "C1", "i", "M3", P * L, "PL, about local 3"),
            _f("PX-V2", "Column shear, V2", "PX", "C1", "i", "V2", P, "P, along local 2"),
            _j("PY-UY", "Top moves along Y (weak axis)", "PY", top, "UY", P * L**3 / (3 * E * I22), "PL^3 / 3EI22"),
            _b("PY-FY", "Base shear Y", "PY", "FY", -P, "-P"),
            _b("PY-MX", "Base moment about X", "PY", "MX", P * L, "PL"),
            _f("PY-M2", "Column moment at base, M2", "PY", "C1", "i", "M2", P * L, "PL, about local 2"),
            _f("PY-V3", "Column shear, V3", "PY", "C1", "i", "V3", P, "P, along local 3"),
            _j("PZ-UZ", "Top shortens", "PZ", top, "UZ", -N * L / (E * A), "-NL / EA"),
            _b("PZ-FZ", "Base vertical reaction", "PZ", "FZ", N, "N"),
            _f("PZ-P", "Column axial force (compression negative)", "PZ", "C1", "i", "P", -N, "-N", how="signed"),
            _j("TZ-RZ", "Top twists", "TZ", top, "RZ", T * L / (G * J), "TL / GJ"),
            _b("TZ-MZ", "Base torque", "TZ", "MZ", -T, "-T"),
            _f("TZ-T", "Column torsion", "TZ", "C1", "i", "T", T, "T"),
        ],
    }


def _j(cid, label, case, at, comp, hand, formula):
    return {"id": cid, "label": label, "case": case, "quantity": "joint", "at": at, "comp": comp,
            "hand": hand, "formula": formula, "how": "signed"}


def _b(cid, label, case, comp, hand, formula):
    return {"id": cid, "label": label, "case": case, "quantity": "base", "comp": comp,
            "hand": hand, "formula": formula, "how": "signed"}


def _f(cid, label, case, frame, station, comp, hand, formula, how="abs"):
    return {"id": cid, "label": label, "case": case, "quantity": "frame", "frame": frame,
            "station": station, "comp": comp, "hand": hand, "formula": formula, "how": how}


def _beam_section(name="BM"):
    return {"name": name, "material": "STEEL", "A": 0.1, "I33": 0.03, "I22": 0.002, "J": 0.0005,
            "As2": 0.0, "As3": 0.0, "depth": 1.5, "width": 0.5}


_COMMON = {
    "frameAxes": "ETABS default (beam local 2 = +Z, column local 2 = +X)",
    "shearDeformation": "off (As2 = As3 = 0)",
    "selfWeight": "none (unit weight 0)",
    "mass": "none; modal case not run",
    "pDelta": "off",
    "rigidZones": "none",
    "endOffsets": "off (ETABS automatic end length offsets disabled, forces reported joint to joint)",
}


def F2() -> dict:
    """Simply supported beam under a uniform load, a joint at midspan."""
    L, w = 30.0, 2.0
    E, I = STEEL_E, 0.03
    mid = [L / 2, 0.0, 12.0]
    return {
        "id": "F2", "title": "Simply supported beam, uniform load",
        "purpose": "Pinned supports, a distributed span load, midspan deflection, end slope, reactions.",
        "units": "kip-ft", "settings": dict(_COMMON, supports="pin at one end, roller at the other"),
        "materials": [{"name": "STEEL", "E": E, "nu": STEEL_NU, "unitWeight": 0.0}],
        "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": mid, "section": "BM", "angle": 0.0},
                   {"id": "B2", "i": mid, "j": [L, 0.0, 12.0], "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": [1, 1, 1, 1, 0, 0]},
                     {"at": [L, 0.0, 12.0], "restraint": [0, 1, 1, 1, 0, 0]}],
        "patterns": [{"name": "W"}],
        "distLoads": [{"pattern": "W", "frame": "B1", "w": w}, {"pattern": "W", "frame": "B2", "w": w}],
        "checks": [
            _j("W-UZ", "Midspan deflection", "W", mid, "UZ", -5 * w * L**4 / (384 * E * I), "-5wL^4 / 384EI"),
            _j("W-RY", "End slope", "W", [0.0, 0.0, 12.0], "RY", w * L**3 / (24 * E * I), "wL^3 / 24EI"),
            _b("W-FZ", "Total vertical reaction", "W", "FZ", w * L, "wL"),
            _f("W-M3", "Midspan moment", "W", "B1", "j", "M3", w * L**2 / 8, "wL^2 / 8"),
            _f("W-V2", "End shear", "W", "B1", "i", "V2", w * L / 2, "wL / 2"),
        ],
    }


def F3() -> dict:
    """Fixed-fixed beam: a uniform load, and separately a point load at midspan."""
    L, w, P = 30.0, 2.0, 20.0
    E, I = STEEL_E, 0.03
    mid = [L / 2, 0.0, 12.0]
    fixed = [1, 1, 1, 1, 1, 1]
    return {
        "id": "F3", "title": "Fixed-fixed beam, uniform and point load",
        "purpose": "Fixed-end moments under a span load and a point load.",
        "units": "kip-ft", "settings": dict(_COMMON, supports="fixed both ends"),
        "materials": [{"name": "STEEL", "E": E, "nu": STEEL_NU, "unitWeight": 0.0}],
        "sections": [_beam_section()],
        "stories": [{"name": "Story1", "height": 12.0}],
        "frames": [{"id": "B1", "i": [0.0, 0.0, 12.0], "j": mid, "section": "BM", "angle": 0.0},
                   {"id": "B2", "i": mid, "j": [L, 0.0, 12.0], "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 12.0], "restraint": fixed}, {"at": [L, 0.0, 12.0], "restraint": fixed}],
        "patterns": [{"name": "W"}, {"name": "P"}],
        "distLoads": [{"pattern": "W", "frame": "B1", "w": w}, {"pattern": "W", "frame": "B2", "w": w}],
        "pointLoads": [{"pattern": "P", "at": mid, "f": [0, 0, -P, 0, 0, 0]}],
        "checks": [
            _j("W-UZ", "Midspan deflection, uniform load", "W", mid, "UZ", -w * L**4 / (384 * E * I), "-wL^4 / 384EI"),
            _f("W-Mend", "End moment, uniform load", "W", "B1", "i", "M3", w * L**2 / 12, "wL^2 / 12"),
            _f("W-Mmid", "Midspan moment, uniform load", "W", "B1", "j", "M3", w * L**2 / 24, "wL^2 / 24"),
            _b("W-FZ", "Total vertical reaction", "W", "FZ", w * L, "wL"),
            _j("P-UZ", "Midspan deflection, point load", "P", mid, "UZ", -P * L**3 / (192 * E * I), "-PL^3 / 192EI"),
            _f("P-Mend", "End moment, point load", "P", "B1", "i", "M3", P * L / 8, "PL / 8"),
            _f("P-Mmid", "Midspan moment, point load", "P", "B1", "j", "M3", P * L / 8, "PL / 8"),
            _b("P-FZ", "Total vertical reaction", "P", "FZ", P, "P"),
        ],
    }


def F4() -> dict:
    """Portal frame, fixed bases, lateral load at the beam (half at each joint): slope-deflection by hand."""
    h, L, H = 12.0, 20.0, 10.0
    E, Ic, Ib = STEEL_E, 0.02, 0.03
    a, b = 2 * E * Ic / h, 6 * E * Ib / L
    # antisymmetric sway: joint rotation t from joint balance, shear per column V = H/2
    k = 3 * a / (h * (2 * a + b))                  # t = k * D
    D = (H / 2) * h / (a * (6 / h - 3 * k))        # V = a (6D/h - 3t) / h
    t = k * D
    Mbase = a * (t - 3 * D / h)
    Mtop = a * (2 * t - 3 * D / h)
    Mbeam = b * t
    col = {"name": "COL", "material": "STEEL", "A": 1.0, "I33": Ic, "I22": 0.005, "J": 0.0005,
           "As2": 0.0, "As3": 0.0, "depth": 1.0, "width": 0.5}
    beam = dict(_beam_section(), A=1.0)
    top_l, top_r = [0.0, 0.0, h], [L, 0.0, h]
    fixed = [1, 1, 1, 1, 1, 1]
    return {
        "id": "F4", "title": "Portal frame, lateral load",
        "purpose": ("Sway of a rigid-jointed frame: column and beam stiffness share the joint rotation. "
                    "Areas are large (1 ft2) so axial shortening does not disturb the slope-deflection answer."),
        "units": "kip-ft", "settings": dict(_COMMON, supports="fixed bases"),
        "materials": [{"name": "STEEL", "E": E, "nu": STEEL_NU, "unitWeight": 0.0}],
        "sections": [col, beam],
        "stories": [{"name": "Story1", "height": h}],
        "frames": [{"id": "C1", "i": [0.0, 0.0, 0.0], "j": top_l, "section": "COL", "angle": 0.0},
                   {"id": "C2", "i": [L, 0.0, 0.0], "j": top_r, "section": "COL", "angle": 0.0},
                   {"id": "B1", "i": top_l, "j": top_r, "section": "BM", "angle": 0.0}],
        "supports": [{"at": [0.0, 0.0, 0.0], "restraint": fixed}, {"at": [L, 0.0, 0.0], "restraint": fixed}],
        "patterns": [{"name": "H"}],
        # H split equally between the two joints: no axial force in the beam, so the
        # antisymmetric slope-deflection solution is exact
        "pointLoads": [{"pattern": "H", "at": top_l, "f": [H / 2, 0, 0, 0, 0, 0]},
                       {"pattern": "H", "at": top_r, "f": [H / 2, 0, 0, 0, 0, 0]}],
        "checks": [
            _j("H-UX", "Sway at the beam", "H", top_l, "UX", D, "slope-deflection"),
            _j("H-RY", "Joint rotation", "H", top_l, "RY", t, "slope-deflection"),
            _b("H-FX", "Base shear", "H", "FX", -H, "-H"),
            _f("H-V2", "Column shear", "H", "C1", "i", "V2", H / 2, "H / 2"),
            _f("H-Mbase", "Column moment at base", "H", "C1", "i", "M3", abs(Mbase), "slope-deflection"),
            _f("H-Mtop", "Column moment at top", "H", "C1", "j", "M3", abs(Mtop), "slope-deflection"),
            _f("H-Mbeam", "Beam end moment", "H", "B1", "i", "M3", abs(Mbeam), "6EIb t / L"),
        ],
    }


CASES = {"F1": F1, "F2": F2, "F3": F3, "F4": F4}


def get(cid: str) -> dict:
    return CASES[cid]()


if __name__ == "__main__":
    out = HERE / "cases"
    out.mkdir(exist_ok=True)
    for cid, fn in CASES.items():
        (out / f"{cid}.json").write_text(json.dumps(fn(), indent=1), encoding="utf-8")
        print(f"wrote cases/{cid}.json")
    (out / "index.json").write_text(json.dumps(list(CASES)), encoding="utf-8")
    print("wrote cases/index.json")
