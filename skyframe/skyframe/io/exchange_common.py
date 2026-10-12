"""Shared helpers for the model-exchange writers / readers (E2K, IFC, DXF,
XLSX): unit systems, number formatting and the "residual patch" diff.

Residual patches
----------------
A native exchange format (ETABS ``.e2k``, IFC, DXF) cannot carry every
SkyFrame field.  The E2K writer therefore re-imports its own native output,
diffs the result against the original ``BuildingModel.to_dict()`` and writes
each remaining difference as a SkyFrame comment line (``$SF {...}``) that
ETABS ignores.  :func:`diff_patches` produces the operations and
:func:`apply_patches` replays them:

* ``{"op": "set", "path": [...], "value": v}`` — set a dict key / list item;
* ``{"op": "del", "path": [...]}``           — delete a dict key;
* ``{"op": "order", "path": [...], "keys": [...]}`` — reorder a dict.

Paths are JSON arrays of dict keys (strings) and list indices (ints) into
the ``to_dict()`` form; values are SI (kN, m, s, tonne, deg C).
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# units: file unit -> SkyFrame SI (kN, m)
# --------------------------------------------------------------------------- #
FORCE_TO_KN = {"KN": 1.0, "N": 1e-3, "KIP": 4.4482216152605,
               "LB": 4.4482216152605e-3, "KGF": 9.80665e-3,
               "TONF": 9.80665}
LENGTH_TO_M = {"M": 1.0, "MM": 1e-3, "CM": 1e-2, "FT": 0.3048,
               "IN": 0.0254}

# display-unit presets offered by the UI (force, length)
UNIT_PRESETS = {"kN-m": ("KN", "M"), "N-mm": ("N", "MM"),
                "kN-mm": ("KN", "MM"), "kip-ft": ("KIP", "FT"),
                "kip-in": ("KIP", "IN"), "lb-in": ("LB", "IN"),
                "kgf-m": ("KGF", "M"), "tonf-m": ("TONF", "M")}

G_SI = 9.80665


class Units:
    """Conversion factors file -> SI for one (force, length) system."""

    def __init__(self, force: str = "KN", length: str = "M"):
        f, l_ = str(force).upper(), str(length).upper()
        if f not in FORCE_TO_KN or l_ not in LENGTH_TO_M:
            raise ValueError(f"unsupported unit system ({force}, {length}); "
                             f"forces {sorted(FORCE_TO_KN)}, lengths "
                             f"{sorted(LENGTH_TO_M)}")
        self.force, self.length = f, l_
        self.F = FORCE_TO_KN[f]
        self.L = LENGTH_TO_M[l_]

    @property
    def is_si(self) -> bool:
        return self.F == 1.0 and self.L == 1.0

    # quantity -> factor (SI value = file value * factor)
    def factor(self, q: str) -> float:
        F, L = self.F, self.L
        return {
            "none": 1.0, "length": L, "area": L ** 2, "inertia": L ** 4,
            "volume": L ** 3, "force": F, "moment": F * L,
            "stress": F / L ** 2, "pressure": F / L ** 2,
            "unit_weight": F / L ** 3, "line_force": F / L,
            "stiffness": F / L, "rot_stiffness": F * L,
            "line_spring": F / L ** 2, "area_spring": F / L ** 3,
            "mass": F / L, "rot_mass": F * L, "mass_density": F / L ** 4,
            "mass_per_length": F / L ** 2, "mass_per_area": F / L ** 3,
            "accel": L,
        }[q]

    def to_si(self, v: float, q: str) -> float:
        k = self.factor(q)
        return float(v) if k == 1.0 else float(v) * k

    def from_si(self, v: float, q: str) -> float:
        k = self.factor(q)
        return float(v) if k == 1.0 else float(v) / k


def fmt(v) -> str:
    """Shortest exact text of a float (``repr``), ``.0`` stripped."""
    r = repr(float(v))
    if r.endswith(".0"):
        r = r[:-2]
    if r == "-0":
        r = "0"
    return r


def q(s) -> str:
    """Quote a name for an e2k record (embedded quotes become '')."""
    return '"' + str(s).replace('"', "''") + '"'


# --------------------------------------------------------------------------- #
# residual diff / patch
# --------------------------------------------------------------------------- #
# derived echo keys never re-read by from_dict (diffing them is noise)
_DERIVED = {("materials", "*", "G"), ("materials", "*", "mass_per_volume"),
            ("materials", "*", "Fye"), ("materials", "*", "Fue"),
            ("members", "*", "length"), ("story_masses",),
            ("grid", "x_labels"), ("grid", "y_labels"),
            ("grid_systems", "*", "x_labels"),
            ("grid_systems", "*", "y_labels")}


def _derived(path: List) -> bool:
    for pat in _DERIVED:
        if len(pat) != len(path):
            continue
        if all(p == "*" or p == k for p, k in zip(pat, path)):
            return True
    return False


def _num_equal(a: float, b: float, rel: float) -> bool:
    if a == b:
        return True
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= rel * max(abs(a), abs(b))


def equal(a, b, rel: float = 0.0) -> bool:
    """Deep equality with an optional relative float tolerance; dict key
    ORDER is part of equality (JSON byte identity)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return (list(a) == list(b)
                and all(equal(a[k], b[k], rel) for k in a))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(equal(x, y, rel)
                                        for x, y in zip(a, b))
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        if rel <= 0.0:
            return type(a) is type(b) and a == b
        return _num_equal(float(a), float(b), rel)
    return type(a) is type(b) and a == b


def diff_patches(native, target, rel: float = 0.0,
                 path: Optional[List] = None) -> List[dict]:
    """Operations turning ``native`` into ``target`` (both to_dict forms)."""
    path = path or []
    ops: List[dict] = []
    if _derived(path):
        return ops
    if equal(native, target, rel):
        return ops
    if isinstance(native, dict) and isinstance(target, dict):
        for k in list(native):
            if k not in target and not _derived(path + [k]):
                ops.append({"op": "del", "path": path + [k]})
        for k in target:
            if k in native:
                ops.extend(diff_patches(native[k], target[k], rel,
                                        path + [k]))
            elif not _derived(path + [k]):
                ops.append({"op": "set", "path": path + [k],
                            "value": target[k]})
        keys_after = [k for k in native if k in target] + \
            [k for k in target if k not in native]
        if keys_after != list(target):
            ops.append({"op": "order", "path": path, "keys": list(target)})
        return ops
    if isinstance(native, list) and isinstance(target, list) \
            and len(native) == len(target) and target \
            and all(isinstance(x, (dict, list)) for x in target):
        for i, (a, b) in enumerate(zip(native, target)):
            ops.extend(diff_patches(a, b, rel, path + [i]))
        return ops
    ops.append({"op": "set", "path": path, "value": target})
    return ops


def _walk(root, path: List):
    cur = root
    for k in path:
        if isinstance(cur, list):
            cur = cur[int(k)]
        else:
            cur = cur.setdefault(k, {})
    return cur


def apply_patches(d: dict, ops: List[dict]) -> dict:
    """Replay :func:`diff_patches` operations onto a copy of ``d``."""
    out = copy.deepcopy(d)
    for op in ops:
        path = list(op.get("path") or [])
        kind = op.get("op")
        if kind == "order":
            obj = _walk(out, path)
            if isinstance(obj, dict):
                keys = [k for k in op.get("keys", []) if k in obj]
                rest = [k for k in obj if k not in keys]
                items = [(k, obj[k]) for k in keys + rest]
                obj.clear()
                obj.update(items)
            continue
        if not path:
            if kind == "set" and isinstance(op.get("value"), dict):
                out = copy.deepcopy(op["value"])
            continue
        parent = _walk(out, path[:-1])
        last = path[-1]
        if kind == "set":
            if isinstance(parent, list):
                idx = int(last)
                while len(parent) <= idx:
                    parent.append(None)
                parent[idx] = copy.deepcopy(op.get("value"))
            else:
                parent[last] = copy.deepcopy(op.get("value"))
        elif kind == "del" and isinstance(parent, dict):
            parent.pop(last, None)
    return out


def summarize_patches(ops: List[dict]) -> Dict[str, int]:
    """{top-level key: number of operations} for a report."""
    out: Dict[str, int] = {}
    for op in ops:
        p = op.get("path") or ["<model>"]
        out[str(p[0])] = out.get(str(p[0]), 0) + 1
    return out


def model_dict(model) -> dict:
    """Normalised ``to_dict`` (via from_dict) of a model or dict."""
    from skyframe.core.model import BuildingModel
    if isinstance(model, dict):
        model = BuildingModel.from_dict(model)
    return model.to_dict()
