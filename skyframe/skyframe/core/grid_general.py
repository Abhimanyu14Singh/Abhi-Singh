"""General (arbitrary line) grid systems — ETABS grid type "General".

CONTRACT "Model templates, general grids, shell editing and drawing aids".

A ``GridSystem`` with ``kind == "general"`` carries ``lines``::

    [{"label": "G1", "p1": [x, y], "p2": [x, y]}, ...]

end points in the grid's LOCAL frame (the system ``origin`` / ``rotation``
apply exactly as for orthogonal grids).  Grids remain drafting aids only.

* drawable lines: each line's two end points mapped to GLOBAL;
* snap points: every line END POINT (label ``"<label>.1"`` / ``".2"``) and
  every proper crossing of two lines (label ``"<a>-<b>"``), computed in the
  local frame by the exact segment-segment parameter solve
  ``p1 + t (p2 - p1) = q1 + u (q2 - q1)``, ``0 <= t, u <= 1``.

Only ``kind == "general"`` grids carry the ``lines`` key in ``to_dict``,
so every existing model stays byte-identical.
"""

from __future__ import annotations

import math
from typing import List, Optional

GENERAL = "general"
_EPS = 1e-12


def _xy(v, what: str):
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool)
                       and math.isfinite(c) for c in v)):
        raise ValueError(f"{what} must be [x, y] finite numbers")
    return [float(v[0]), float(v[1])]


def normalize_lines(lines) -> List[dict]:
    """Validated copy of a ``lines`` list (labels default ``L<n>``)."""
    if lines is None:
        return []
    if not isinstance(lines, list):
        raise ValueError("general grid 'lines' must be a list")
    out, seen = [], set()
    for i, ln in enumerate(lines):
        if not isinstance(ln, dict):
            raise ValueError(f"general grid line {i + 1} must be an object")
        lab = ln.get("label") or f"L{i + 1}"
        if not isinstance(lab, str) or not lab.strip():
            raise ValueError(f"general grid line {i + 1}: bad label")
        lab = lab.strip()
        if lab in seen:
            raise ValueError(f"general grid: duplicate line label {lab!r}")
        seen.add(lab)
        p1 = _xy(ln.get("p1"), f"line {lab} p1")
        p2 = _xy(ln.get("p2"), f"line {lab} p2")
        if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1e-9:
            raise ValueError(f"general grid line {lab}: zero length")
        out.append({"label": lab, "p1": p1, "p2": p2})
    return out


def validate(g) -> None:
    """Model hook: a general grid needs at least one valid line."""
    lines = normalize_lines(getattr(g, "lines", None))
    if not lines:
        raise ValueError(f"Grid {g.name!r}: a general grid needs at least "
                         "one line")


def lines_to_dict(g) -> dict:
    """``{"lines": [...]}`` for general grids, ``{}`` otherwise."""
    if getattr(g, "kind", "") != GENERAL:
        return {}
    return {"lines": [{"label": ln["label"], "p1": [float(v) for v in
                                                      ln["p1"]],
                       "p2": [float(v) for v in ln["p2"]]}
                      for ln in (g.lines or [])]}


def lines_from_dict(d: dict) -> List[dict]:
    if d.get("kind") != GENERAL:
        return []
    return normalize_lines(d.get("lines"))


def lines_global(g) -> List[dict]:
    return [{"label": ln["label"],
             "points": [list(g.to_global(*ln["p1"])),
                        list(g.to_global(*ln["p2"]))]}
            for ln in (g.lines or [])]


def segment_intersection(p1, p2, q1, q2, eps: float = 1e-9
                         ) -> Optional[tuple]:
    """Proper crossing ``(t, u, (x, y))`` of segments p1p2 and q1q2 (both
    parameters in [0, 1] within ``eps``), or None (parallel / disjoint)."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    den = rx * sy - ry * sx
    if abs(den) < _EPS * max(1.0, math.hypot(rx, ry) * math.hypot(sx, sy)):
        return None
    qpx, qpy = q1[0] - p1[0], q1[1] - p1[1]
    t = (qpx * sy - qpy * sx) / den
    u = (qpx * ry - qpy * rx) / den
    if -eps <= t <= 1 + eps and -eps <= u <= 1 + eps:
        return t, u, (p1[0] + t * rx, p1[1] + t * ry)
    return None


def intersections_global(g) -> List[dict]:
    out: List[dict] = []
    lines = g.lines or []
    for ln in lines:
        for k, key in ((1, "p1"), (2, "p2")):
            out.append({"label": f"{ln['label']}.{k}",
                        "point": list(g.to_global(*ln[key]))})
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            a, b = lines[i], lines[j]
            hit = segment_intersection(a["p1"], a["p2"], b["p1"], b["p2"])
            if hit is None:
                continue
            out.append({"label": f"{a['label']}-{b['label']}",
                        "point": list(g.to_global(*hit[2]))})
    return out
