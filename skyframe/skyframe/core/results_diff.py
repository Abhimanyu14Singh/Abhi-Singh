"""User-facing results comparison (Tools > Compare Results..., Wave C).

Builds on :mod:`skyframe.core.results_compare` (the equivalence-test
comparator): the same per-family scale ``S`` (largest magnitude of the
quantity family across reference run A) and the same modal sign / cluster
alignment, but returns a structured DIFF TABLE instead of a pass/fail
list, filtered by case and quantity.

A value pair is flagged when ``|a - b| > max(atol, rtol * max(|a|, |b|,
S))``.  Rows (sorted by relative difference, largest first)::

    {"path": "cases.DEAD.node_disp.12.0", "block": "cases",
     "case": "DEAD", "quantity": "node_disp", "item": "12.0",
     "a": 1.0e-3, "b": 1.1e-3, "diff": 1.0e-4, "rel": 0.0909,
     "status": "changed" | "only_a" | "only_b" | "type"}

``only_a`` / ``only_b`` rows name a subtree present in one run only (one
row per subtree, ``a`` / ``b`` = number of numeric values in it).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import results_compare as _rc

# name-keyed blocks whose level 2 is a quantity (case-shaped results)
CASE_BLOCKS = ("cases", "combos", "rs_cases", "th_cases", "staged",
               "nonlinear_static", "pushover")
NAME_BLOCKS = tuple(dict.fromkeys(_rc.NAME_KEYED + ("spandrels",)))
SKIP_TOP = ("model_name", "nodes", "members", "supports", "shell_quads",
            "story_order", "story_elev", "case_status", "combo_status")


def unwrap(obj) -> dict:
    """A results dict from a results dict or a saved wrapper
    (``{"results": {...}}`` / ``{"data": {"results": ...}}``)."""
    if not isinstance(obj, dict):
        raise ValueError("results must be a JSON object")
    for _ in range(3):
        if any(k in obj for k in ("cases", "modal", "rs_cases",
                                  "th_cases")):
            return obj
        inner = obj.get("results")
        if not isinstance(inner, dict) and isinstance(obj.get("data"), dict):
            inner = obj["data"].get("results")
        if not isinstance(inner, dict):
            break
        obj = inner
    raise ValueError("not an analysis-results object (no 'cases' / "
                     "'modal' block)")


def _classify(path: Tuple) -> Tuple[str, str, str, str]:
    """(block, case, quantity, item) of a key path."""
    block = str(path[0]) if path else ""
    case = ""
    rest = list(path[1:])
    if block in NAME_BLOCKS and rest:
        case = str(rest.pop(0))
    if block in CASE_BLOCKS and rest:
        quantity = str(rest.pop(0))
        if quantity == "min" and rest:          # envelope minima
            quantity = f"min.{rest.pop(0)}"
    else:
        quantity = block
    return block, case, quantity, ".".join(str(p) for p in rest)


def _count(obj) -> int:
    return sum(1 for _p, v in _rc._walk(obj)
               if isinstance(v, (int, float)) and not isinstance(v, bool))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def catalogue(*results: dict) -> dict:
    """Cases and quantities present in the given results (filter lists)."""
    cases: List[str] = []
    qty: List[str] = []
    for r in results:
        for block, v in (r or {}).items():
            if block in SKIP_TOP:
                continue
            if block in NAME_BLOCKS and isinstance(v, dict):
                for name, sub in v.items():
                    if name not in cases:
                        cases.append(name)
                    if block in CASE_BLOCKS and isinstance(sub, dict):
                        for q in sub:
                            if q not in qty and q != "min":
                                qty.append(q)
                    elif block not in qty:
                        qty.append(block)
            elif block not in qty:
                qty.append(block)
    return {"cases": cases, "quantities": qty}


def diff(a, b, *, case: Optional[str] = None,
         quantity: Optional[str] = None, rtol: float = 1e-6,
         atol: float = 0.0, max_rows: int = 500,
         modal_invariant: bool = True) -> dict:
    """Diff table of run ``b`` against reference run ``a``."""
    a, b = unwrap(a), unwrap(b)
    if not (math.isfinite(rtol) and rtol >= 0.0):
        raise ValueError("rtol must be a finite value >= 0")
    if not (math.isfinite(atol) and atol >= 0.0):
        raise ValueError("atol must be a finite value >= 0")
    if modal_invariant:
        a, b = _rc.align_modal(a, b)
    sc = _rc._scales(a)
    rows: List[dict] = []
    stats = {"compared": 0, "flagged": 0, "worst_rel": 0.0}

    def prune(path) -> bool:
        """True when nothing below ``path`` can pass the filters."""
        if not path:
            return False
        top = str(path[0])
        if top in SKIP_TOP:
            return True
        if case and top not in NAME_BLOCKS:
            return True
        if case and len(path) >= 2 and str(path[1]) != case:
            return True
        if quantity and top not in CASE_BLOCKS and top != quantity:
            return True
        if quantity and top in CASE_BLOCKS and len(path) >= 3:
            q = _classify(path)[2]
            if q != quantity and q != "min." + quantity and \
                    str(path[2]) != "min":
                return True
        return False

    def keep(path) -> bool:
        return not prune(path)

    def row(path, va, vb, status, d=0.0, rel=0.0):
        blk, cs, q, item = _classify(path)
        rows.append({"path": ".".join(str(p) for p in path), "block": blk,
                     "case": cs, "quantity": q, "item": item, "a": va,
                     "b": vb, "diff": d, "rel": rel, "status": status})

    def rec(x, y, path):
        if prune(path):
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in x:
                p = path + (str(k),)
                if k not in y:
                    if keep(p):
                        n = _count(x[k])
                        stats["flagged"] += 1
                        row(p, n, None, "only_a")
                    continue
                rec(x[k], y[k], p)
            for k in y:
                if k not in x and keep(path + (str(k),)):
                    stats["flagged"] += 1
                    row(path + (str(k),), None, _count(y[k]), "only_b")
            return
        if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
            if len(x) != len(y):
                stats["flagged"] += 1
                row(path, len(x), len(y), "length")
            for i, (u, v) in enumerate(zip(x, y)):
                rec(u, v, path + (i,))
            return
        if _num(x) and _num(y):
            stats["compared"] += 1
            fa, fb = float(x), float(y)
            if not (math.isfinite(fa) and math.isfinite(fb)):
                if not (fa == fb or (math.isnan(fa) and math.isnan(fb))):
                    stats["flagged"] += 1
                    row(path, fa, fb, "changed", math.inf, math.inf)
                return
            s = max(abs(fa), abs(fb), _rc._scale(sc, path))
            d = abs(fa - fb)
            rel = d / s if s > 0.0 else 0.0
            stats["worst_rel"] = max(stats["worst_rel"], rel)
            if d > max(atol, rtol * s):
                stats["flagged"] += 1
                row(path, fa, fb, "changed", d, rel)
            return
        if isinstance(x, (dict, list, tuple)) or \
                isinstance(y, (dict, list, tuple)) or _num(x) != _num(y):
            stats["flagged"] += 1
            row(path, str(type(x).__name__), str(type(y).__name__), "type")
            return
        if x != y:
            stats["flagged"] += 1
            row(path, str(x), str(y), "changed", 0.0, 0.0)

    rec(a, b, ())
    order = {"changed": 0, "type": 1, "length": 2, "only_a": 3, "only_b": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 9),
                             -(r["rel"] if math.isfinite(r["rel"]) else 1e300),
                             r["path"]))
    cat = catalogue(a, b)
    return {"equal": stats["flagged"] == 0, "n_compared": stats["compared"],
            "n_flagged": stats["flagged"], "worst_rel": stats["worst_rel"],
            "rtol": rtol, "atol": atol, "truncated": len(rows) > max_rows,
            "rows": [_clean(r) for r in rows[:max_rows]],
            "cases": cat["cases"], "quantities": cat["quantities"]}


def _clean(r: dict) -> dict:
    for k in ("diff", "rel"):
        v = r.get(k)
        if isinstance(v, float) and not math.isfinite(v):
            r[k] = None
    return r


def summarize(rows: Sequence[dict]) -> Dict[str, int]:
    """Flag counts per quantity (for the dialog's summary line)."""
    out: Dict[str, int] = {}
    for r in rows:
        out[r["quantity"]] = out.get(r["quantity"], 0) + 1
    return out
