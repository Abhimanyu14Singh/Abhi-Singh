"""HTTP endpoint of Tools > Compare Results... (Wave C,
:mod:`skyframe.core.results_diff`); registered by ``create_app`` with one
line.  CONTRACT "Results comparison (Compare Results)".

``POST /api/results/compare`` body::

    {"a": <results dict> | "current", "b": <results dict> | "current",
     "case": "", "quantity": "", "rtol": 1e-6, "atol": 0.0,
     "max_rows": 500}

``"current"`` = the stored results of the last ``/api/analyze`` of the
current model (400 when there are none).  A results dict may also be a
saved wrapper ``{"results": {...}}``.  Returns the diff dict of
:func:`skyframe.core.results_diff.diff`.  Never mutates the model.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from flask import jsonify, request

from skyframe.core import results_diff as _rd


def register(app, state: Dict[str, Any], lookup: Callable) -> None:

    def _side(v):
        if v == "current":
            res, _ctx = lookup(state["model"])
            if res is None:
                raise ValueError("no current results: run the analysis "
                                 "first")
            return res
        return _rd.unwrap(v)

    @app.post("/api/results/compare")
    def results_compare():
        """Diff table of two analysis runs / saved result files (rows of
        changed values with relative difference; case / quantity filters,
        rtol / atol tolerance)."""
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "body must be a JSON object"}), 400
        try:
            a = _side(body.get("a"))
            b = _side(body.get("b"))
            rtol = float(body.get("rtol", 1e-6))
            atol = float(body.get("atol", 0.0))
            max_rows = int(body.get("max_rows", 500))
            if not 1 <= max_rows <= 20000:
                raise ValueError("max_rows must be in [1, 20000]")
            out = _rd.diff(a, b, case=str(body.get("case") or "") or None,
                           quantity=str(body.get("quantity") or "") or None,
                           rtol=rtol, atol=atol, max_rows=max_rows)
            return jsonify(out)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
