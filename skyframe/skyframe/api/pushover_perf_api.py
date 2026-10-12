"""HTTP endpoint: pushover performance methods + ADRS (CONTRACT "Pushover
performance methods (ATC-40, FEMA 440 EL, EN 1998 N2) and the ADRS
plot").  Registered by ``create_app`` with one line; it never mutates
the current model.

* ``POST /api/results/pushover-performance`` ``{case, performance?,
  model?}`` -> the performance block of
  :func:`skyframe.engine.pushover_performance.evaluate` (``options``,
  ``modal``, ``spectrum``, ``capacity``, ``demand``, ``methods``,
  ``summary``, ``warnings``) plus ``case``, ``direction`` and ``curve``
  ``{disp, base_shear}``.  ``performance`` overrides the case's stored
  options (missing keys -> defaults).  400 on an unknown case, bad
  options or a curve with fewer than 2 converged steps.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

_ERRORS = (ValueError, KeyError, TypeError, ZeroDivisionError)


def register(app, state: Dict[str, Any]) -> None:

    @app.post("/api/results/pushover-performance")
    def results_pushover_performance():
        try:
            from skyframe.engine.opensees_engine import OpenSeesEngine
        except Exception:                          # pragma: no cover
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.core.model import BuildingModel
        from skyframe.core import pushover_methods as _pm
        from skyframe.engine import pushover_performance as _ppf
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        try:
            model = (BuildingModel.from_dict(body["model"])
                     if isinstance(body.get("model"), dict)
                     else state["model"])
        except _ERRORS as exc:
            return jsonify({"error": f"model: {exc}"}), 400
        name = body.get("case")
        if name not in model.pushover_cases:
            return jsonify({"error": f"Unknown pushover case {name!r}"}), 400
        case = model.pushover_cases[name]
        opts = body.get("performance", case.performance)
        try:
            _pm.normalize_options(opts)
            engine = OpenSeesEngine(model)
            po = engine.run_pushover(name)
            if len(po.roof_disp) < 2:
                return jsonify({"error": "pushover produced fewer than 2 "
                                         "converged steps"}), 400
            out = _ppf.evaluate(engine, case, po, options=opts or {})
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
        out.update({"case": name, "direction": case.direction,
                    "curve": {"disp": list(po.roof_disp),
                              "base_shear": list(po.base_shear)}})
        return jsonify(out)
