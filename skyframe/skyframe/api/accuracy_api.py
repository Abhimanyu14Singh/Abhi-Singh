"""HTTP endpoints of the mesh-sensitivity study and the accuracy advisor
(:mod:`skyframe.core.mesh_study`, :mod:`skyframe.core.accuracy_advisor`);
registered by ``create_app`` with one line (CONTRACT "Mesh sensitivity
study and accuracy advisor").

Neither endpoint ever replaces or mutates the server's current model.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

from skyframe.core.model import BuildingModel


def register(app, state: Dict[str, Any]) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    @app.post("/api/analyze/mesh_study")
    def analyze_mesh_study():
        """Body ``{levels?: [int] (default [1, 2, 4]), cases?: [names]
        (default every linear static case + MODAL), quantities?: [...]
        (default all), tolerance_pct?: float (default 5), max_elements?:
        int (default 20000), model?: <model dict>}``.  400 on bad input."""
        try:
            from skyframe.engine import opensees_engine as _ose  # noqa: F401
        except Exception:                         # pragma: no cover
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.core import mesh_study as _ms
        body = _body()
        try:
            model = (BuildingModel.from_dict(body["model"])
                     if body.get("model") is not None else state["model"])
            res = _ms.mesh_study(
                model, levels=body.get("levels"), cases=body.get("cases"),
                quantities=body.get("quantities"),
                tolerance_pct=body.get("tolerance_pct",
                                       _ms.DEFAULT_TOLERANCE_PCT),
                max_elements=body.get("max_elements",
                                      _ms.DEFAULT_MAX_ELEMENTS))
        except (ValueError, KeyError, TypeError, RuntimeError,
                ArithmeticError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(res)

    @app.route("/api/accuracy_advice", methods=["GET", "POST"])
    def accuracy_advice_endpoint():
        """GET: advice for the current model.  POST body ``{model?,
        thresholds?: {aspect_max, warp_max, min_elements_per_span,
        thick_ratio, thin_ratio, opening_ratio}}``."""
        from skyframe.core.accuracy_advisor import accuracy_advice
        body = _body() if request.method == "POST" else {}
        try:
            if body.get("model") is not None:
                from skyframe.core.checks import load_model_lenient
                model = load_model_lenient(body["model"])
            else:
                model = state["model"]
            return jsonify(accuracy_advice(model, body.get("thresholds")))
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
