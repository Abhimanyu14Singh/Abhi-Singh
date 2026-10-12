"""HTTP endpoints of the shell force / stress contours (A3,
:mod:`skyframe.core.shell_results`); registered by ``create_app`` with one
line.  CONTRACT "Shell force contours and shell stresses".

Results come from the body (``results``) or from the analysis-results store
of the last ``/api/analyze`` (the same store-and-compute rule as
``/api/tables/<key>``); without either, the model is analysed once and the
payload stored.  Never mutates the current model.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from flask import jsonify, request

from skyframe.core import shell_results as _sr


def register(app, state: Dict[str, Any], lookup: Callable,
             remember: Callable) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _results(body: dict):
        if isinstance(body.get("results"), dict):
            return body["results"]
        res, _ctx = lookup(state["model"])
        if res is None:
            from skyframe.engine.opensees_engine import OpenSeesEngine
            engine = OpenSeesEngine(state["model"])
            res = engine.run().to_dict()
            remember(res, engine)
        return res

    @app.route("/api/shell_results/options", methods=["GET", "POST"])
    def shell_results_options():
        """Static option lists: components (with quantity), locations, axes,
        averaging, bounds."""
        return jsonify({
            "components": [{"key": k, "family": f, "quantity": q}
                           for k, (f, _s, q) in _sr.COMPONENTS.items()
                           if k in _sr.CONTOUR_COMPONENTS],
            "locations": list(_sr.FACES), "axes": list(_sr.AXES),
            "averaging": list(_sr.AVERAGING), "bounds": list(_sr.BOUNDS)})

    @app.post("/api/shell_results/sources")
    def shell_results_sources():
        """Body ``{results?}`` -> ``{sources: [{name, kind, bounded}],
        layers: max layer count}``."""
        body = _body()
        try:
            res = _results(body)
            geo = _sr.Geometry(state["model"], res)
            nl = max((len(geo.layers(i)) for i in range(geo.n)), default=0)
            return jsonify({"sources": _sr.list_sources(state["model"], res),
                            "layers": nl, "n_elements": geo.n})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/shell_results/contour")
    def shell_results_contour():
        """Body ``{case, component, location?, axes?, averaging?, bound?,
        results?}`` -> contour dict; 400 on bad input / no shell results."""
        body = _body()
        case = body.get("case")
        if not isinstance(case, str) or not case:
            return jsonify({"error": "'case' must be a case or combo name"}), 400
        try:
            res = _results(body)
            return jsonify(_sr.contour(
                state["model"], res, case, str(body.get("component", "M11")),
                location=str(body.get("location", "top")),
                axes=str(body.get("axes", "local")),
                averaging=str(body.get("averaging", "none")),
                bound=str(body.get("bound", "max"))))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
