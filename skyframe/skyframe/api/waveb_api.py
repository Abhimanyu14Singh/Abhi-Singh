"""HTTP endpoints of the Wave B loose ends (registered by ``create_app``
with one line).  CONTRACT "Irregularity checks, grouped ASCE 7
combinations, E2K releases / joint axes, joint-local reactions, RS shell
forces in combos, TS 498 / KDS / CFE wind, NTC long-period distribution".

* ``POST /api/irregularity``  body ``{options?, results?}`` ->
  :func:`skyframe.core.irregularity_ext.compute` report.  Without results
  in the body the last analysis is used (``/api/tables`` store); when there
  is none, only the geometric / mass / strength checks run (the
  response-based rows are ``n/a``) -- the model is NOT analysed.
* ``POST /api/results/joint-local-reactions``  body ``{case?, results?}``
  -> ``{"cases": {case: {tag: {point, angles, global, local}}}}``.

Never mutates the current model.
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from flask import jsonify, request

from skyframe.core import irregularity_ext as _irr
from skyframe.core import joint_local_results as _jlr


def register(app, state: Dict[str, Any], lookup: Callable) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _results(body: dict):
        if isinstance(body.get("results"), dict):
            return body["results"]
        res, _ctx = lookup(state["model"])
        return res

    @app.post("/api/irregularity")
    def irregularity_checks():
        body = _body()
        opts = body.get("options") or {}
        if not isinstance(opts, dict):
            return jsonify({"error": "options must be an object"}), 400
        try:
            res = _results(body)
            rep = _irr.compute(state["model"], res, opts)
            rep["has_results"] = bool(res)
            return jsonify(rep)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/results/joint-local-reactions")
    def joint_local_reactions():
        body = _body()
        case = body.get("case")
        if case is not None and not isinstance(case, str):
            return jsonify({"error": "case must be a string"}), 400
        try:
            res = _results(body) or {}
            return jsonify({"cases": _jlr.local_reactions(
                state["model"], res, case)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
