"""Shared constants and check builders for the verification definitions (kip, ft)."""

from __future__ import annotations

STEEL_E = 29000.0 * 144.0          # ksf (29,000 ksi)
STEEL_NU = 0.3


def _j(cid, label, case, at, comp, hand, formula, how="signed"):
    return {"id": cid, "label": label, "case": case, "quantity": "joint", "at": at, "comp": comp,
            "hand": hand, "formula": formula, "how": how}


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


def _mat():
    return [{"name": "STEEL", "E": STEEL_E, "nu": STEEL_NU, "unitWeight": 0.0}]


def _col_section(**over):
    return dict({"name": "COL", "material": "STEEL", "A": 0.1, "I33": 0.02, "I22": 0.005, "J": 0.0005,
                 "As2": 0.0, "As3": 0.0, "depth": 1.0, "width": 0.5}, **over)
