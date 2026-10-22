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


CASES = {"F1": F1}


def get(cid: str) -> dict:
    return CASES[cid]()


if __name__ == "__main__":
    out = HERE / "cases"
    out.mkdir(exist_ok=True)
    for cid, fn in CASES.items():
        (out / f"{cid}.json").write_text(json.dumps(fn(), indent=1), encoding="utf-8")
        print(f"wrote cases/{cid}.json")
