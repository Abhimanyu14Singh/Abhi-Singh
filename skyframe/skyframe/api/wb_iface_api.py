"""HTTP endpoints: structural model templates and the .rtf report
(CONTRACT "Model templates, general grids, shell editing and drawing
aids").  Registered by ``create_app`` with one line.

* ``GET  /api/model/templates`` -> ``[{id, label, params}]``
* ``POST /api/model/template``  ``{template, params?, dry_run?}`` ->
  ``{model, summary}``; the stored model is replaced unless ``dry_run``
  (new models use the ETABS frame-axis convention like ``/api/model/quick``)
* ``POST /api/report/rtf``      the ``/api/report/docx`` document ->
  ``{ok, mime, filename, content_b64, size}``

Shell editing, ramps, wall stacks and Auto Relabel are ``POST
/api/edit/<op>`` operations (:mod:`skyframe.core.edit_shells`); general
grids go through ``POST /api/grid`` / ``POST /api/model``.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict

from flask import jsonify, request


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def register(app, state: Dict[str, Any]) -> None:

    @app.get("/api/model/templates")
    def model_templates():
        from skyframe.core.templates import catalogue
        return jsonify(catalogue())

    @app.post("/api/model/template")
    def model_template():
        from skyframe.core import frame_axes as _fa
        from skyframe.core.templates import build_template
        body = _body()
        tpl = body.get("template")
        params = body.get("params")
        if params is not None and not isinstance(params, dict):
            return jsonify({"error": "'params' must be an object"}), 400
        try:
            model, summary = build_template(tpl, params)
            _fa.as_new_model(model)
            out = model.to_dict()
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        if not body.get("dry_run"):
            state["model"] = model
        return jsonify({"model": out, "summary": summary})

    @app.post("/api/report/rtf")
    def report_rtf():
        from skyframe.io.rtf import MIME, build_rtf
        body = _body()
        try:
            data = build_rtf(body)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        name = re.sub(r"[^\w.-]+", "_",
                      getattr(state["model"], "name", "") or "model")
        return jsonify({"ok": True, "mime": MIME,
                        "filename": f"{name}_report.rtf",
                        "content_b64": base64.b64encode(data).decode("ascii"),
                        "size": len(data)})
