"""Max / Min rows for the Pier / Spandrel / Section Cut Forces tables
(Wave C, engine/cut_results.py).

Hooked into :func:`skyframe.core.tables.compute_table` (one decorate line,
like the joint-local / tower decorators).  Results without max/min cut
entries and without RS / TH / nonlinear / staged names come back
UNCHANGED (legacy tables byte-identical).  Otherwise:

* a ``step_type`` column ("Step Type") follows ``case_type``;
* every row of a max/min entry becomes a ``Max`` row and a ``Min`` row
  (ETABS "Step Type"); the pier table's tension-positive ``P`` swaps the
  bounds (``Max P = -min(P_compression)``);
* ``case_type`` names the case kind: ``LinRespSpec`` (RS and RS
  directional combos), ``LinDirHist`` / ``NonDirHist`` (time history),
  ``NonStatic`` (nonlinear static), ``NonStaged`` (staged).
"""

from __future__ import annotations

from typing import List, Tuple

_BLOCKS = {"pier_forces": "piers", "spandrel_forces": "spandrels",
           "section_cut_forces": "section_cuts"}
_CUT_MAP = (("F1", "FX"), ("F2", "FY"), ("F3", "FZ"), ("M1", "MX"),
            ("M2", "MY"), ("M3", "MZ"))
_SPD = ("P", "V2", "V3", "T", "M2", "M3")


def _kind_type(model, name: str):
    if name in (getattr(model, "rs_cases", None) or {}) or \
            name in (getattr(model, "rs_combos", None) or {}):
        return "LinRespSpec"
    th = (getattr(model, "th_cases", None) or {}).get(name)
    if th is not None:
        return "NonDirHist" if getattr(th, "nonlinear", False) \
            else "LinDirHist"
    if name in (getattr(model, "nonlinear_static_cases", None) or {}):
        return "NonStatic"
    if name in (getattr(model, "staged_cases", None) or {}):
        return "NonStaged"
    return None


def _entry(key: str, data: dict, row: dict):
    try:
        if key == "piers":
            return data[row["case"]][row["pier"]][row["story"]]
        if key == "spandrels":
            return data[row["case"]][row["spandrel"]][row["story"]]
        return data[row["case"]][row["cut"]]
    except (KeyError, TypeError):
        return None


def _bounds(key: str, e: dict, row: dict) -> Tuple[dict, dict]:
    """(Max row values, Min row values) of a max/min entry."""
    mn = e["min"]
    if key == "piers":
        return ({"P": -float(mn["P"]), "V2": float(e["V"]),
                 "M3": float(e["M"])},
                {"P": -float(e["P"]), "V2": float(mn["V"]),
                 "M3": float(mn["M"])})
    if key == "spandrels":
        loc = row["location"]
        return ({k: float(e[loc][k]) for k in _SPD},
                {k: float(mn[loc][k]) for k in _SPD})
    return ({c: float(e[k]) for c, k in _CUT_MAP},
            {c: float(mn[k]) for c, k in _CUT_MAP})


def decorate(key: str, results: dict, model, cols: List[dict],
             rows: List[dict]) -> Tuple[List[dict], List[dict]]:
    blk = _BLOCKS.get(key)
    if blk is None or not rows:
        return cols, rows
    data = results.get(blk) or {}
    names = {r["case"] for r in rows}
    has_min = any(isinstance(_entry(blk, data, r), dict)
                  and "min" in _entry(blk, data, r) for r in rows)
    types = {n: _kind_type(model, n) for n in names}
    if not has_min and not any(types.values()):
        return cols, rows
    out: List[dict] = []
    for r in rows:
        if types.get(r["case"]):
            r = dict(r, case_type=types[r["case"]])
            if blk == "piers" and types[r["case"]] == "LinRespSpec":
                r["P"] = abs(float(r["P"]))      # RS: positive envelope
        e = _entry(blk, data, r)
        if has_min and isinstance(e, dict) and "min" in e:
            hi, lo = _bounds(blk, e, r)
            out.append(dict(r, step_type="Max", **hi))
            out.append(dict(r, step_type="Min", **lo))
        elif has_min:
            out.append(dict(r, step_type=""))
        else:
            out.append(r)
    if has_min:
        new_cols: List[dict] = []
        for c in cols:
            new_cols.append(c)
            if c.get("key") == "case_type":
                new_cols.append({"key": "step_type", "label": "Step Type",
                                 "quantity": "text"})
        cols = new_cols
    return cols, out
