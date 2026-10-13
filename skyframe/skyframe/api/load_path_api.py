"""HTTP endpoint of Tools > Explain Result... (core/load_path.py);
registered by ``create_app`` with one line.  CONTRACT "Load-path
explainer".

``POST /api/explain`` body::

    {"target": {"kind": "reaction", "joint": "12", "component": "FZ"},
     "case": "1.2D + 1.6L", "expand": true, "results"?: {...}}

Uses the stored results of the last ``/api/analyze`` of the current model
(409 when there are none or the model changed since), or an explicit
``results`` dict.  Per-pattern responses that the results do not hold are
re-solved on the current model (linear cases only) and cached per model
signature.  Returns :func:`skyframe.core.load_path.explain`.  Never
mutates the model or the stored results.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict

from flask import jsonify, request

from skyframe.core import load_path as _lp


def register(app, state: Dict[str, Any], lookup: Callable,
             sig: Callable) -> None:

    @app.post("/api/explain")
    def explain_result():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "body must be a JSON object"}), 400
        model = state["model"]
        res = body.get("results")
        if isinstance(res, dict) and isinstance(res.get("results"), dict):
            res = res["results"]
        if not isinstance(res, dict):
            res, _ctx = lookup(model)
            if res is None:
                return jsonify({"error": "no current results for this "
                                "model: run the analysis first (F5)"}), 409
        try:
            ck = hashlib.sha256(sig(model).encode("utf-8")).hexdigest()
            out = _lp.explain(model, res, body.get("target"),
                              str(body.get("case") or ""),
                              expand=bool(body.get("expand", True)),
                              cache_key=ck)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:                  # solver failure
            return jsonify({"error": f"re-solve failed: {exc}"}), 400
        return jsonify(out)
