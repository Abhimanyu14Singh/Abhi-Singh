"""ETABS GENERAL grid lines in ``.e2k`` (Wave C).

CONTRACT "Wave C interface: general grids, explorer, shells with openings,
snapping, units, auto-save, model lock".

A SkyFrame ``GridSystem`` of ``kind == "general"`` is written as an ETABS
grid system of type GENERAL whose lines are given by their two end points
(in the system's LOCAL frame, display length units)::

    GRIDSYSTEM "GL1"  TYPE "GENERAL"  BUBBLESIZE 1.25  UX 2  ROTATION 30
    GRID "GL1"  LABEL "L1"  DIR "GENERAL"  X1 0  Y1 0  X2 6  Y2 3  VISIBLE "Yes"  BUBBLELOC "End"

The reader accepts ``DIR "GENERAL"`` / ``"GEN"`` lines (and a GRIDSYSTEM
``TYPE "GENERAL"``); a system that only has general lines becomes a
``kind: "general"`` system, so export -> import reproduces it without the
``$SF`` extension lines.
"""

from __future__ import annotations

from typing import Optional

from skyframe.io.exchange_common import q

GENERAL_TYPE = "GENERAL"


def is_general_type(typ: str) -> bool:
    return (typ or "").upper().startswith("GEN")


def write_lines(w, g) -> None:
    """GRID records of a general system (``w`` is the e2k writer)."""
    for ln in g.lines or []:
        x1, y1 = ln["p1"]
        x2, y2 = ln["p2"]
        w.rec(f"GRID {q(g.name)}  LABEL {q(ln['label'])}  DIR \"GENERAL\"  "
              f"X1 {w.n(x1, 'length')}  Y1 {w.n(y1, 'length')}  "
              f"X2 {w.n(x2, 'length')}  Y2 {w.n(y2, 'length')}  "
              "VISIBLE \"Yes\"  BUBBLELOC \"End\"")


def read_line(r, g: dict, kv: dict, t) -> Optional[dict]:
    """Append one ``DIR "GENERAL"`` GRID row to grid dict ``g``."""
    vals = [r.si(kv.get(k), "length") for k in ("X1", "Y1", "X2", "Y2")]
    if any(v is None for v in vals):
        return None
    lab = kv["LABEL"].text if "LABEL" in kv else f"L{len(g.get('lines', [])) + 1}"
    g["kind"] = "general"
    ln = {"label": lab, "p1": [vals[0], vals[1]], "p2": [vals[2], vals[3]]}
    g.setdefault("lines", []).append(ln)
    return ln
