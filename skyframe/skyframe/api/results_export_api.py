"""HTTP endpoints: analysis-results export (Excel / SQLite) and the Word
report writer; registered by ``create_app`` with one line (CONTRACT
"Results tables export, spandrels and Word report").

* ``POST /api/tables/export/<fmt>`` (fmt = ``xlsx`` | ``sqlite``), body
  ``{tables?: [keys], cases?: [names], units?: "kN-m", skip_empty?: bool,
  results?: <analyze dict>}`` -> ``{ok: true, filename, mime, content_b64,
  report}``.  Tables are computed from the STORED results of the last
  ``/api/analyze`` (the model is analysed once when none are stored, as
  ``POST /api/tables/<key>`` does).
* ``POST /api/report/docx`` body = the :func:`skyframe.io.docx.build_docx`
  document ``{title, subtitle?, meta?, paper?, landscape?, toc?, blocks}``
  -> ``{ok: true, filename, mime, content_b64, size}``.

Errors: 400 ``{error}``; 404 for an unknown table key.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict

from flask import jsonify, request

EXPORT_MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml."
            "sheet",
    "sqlite": "application/vnd.sqlite3",
}


def _fname(model, suffix: str) -> str:
    name = re.sub(r"[^\w.-]+", "_", getattr(model, "name", "") or "model")
    return f"{name}{suffix}"


def register(app, state: Dict[str, Any]) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _results(model, body):
        if isinstance(body.get("results"), dict):
            return body["results"], None
        from skyframe.api import server as _srv
        res, ctx = _srv._tables_lookup(model)
        if res is None:
            if not _srv._OPENSEES_OK:
                raise RuntimeError("OpenSeesPy is not available")
            engine = _srv.OpenSeesEngine(model)
            res = engine.run().to_dict()
            ctx = _srv._tables_remember(res, engine)
        return res, ctx

    @app.post("/api/tables/export/<fmt>")
    def tables_export(fmt: str):
        from skyframe.core import tables as _tables
        from skyframe.io import results_export as RX
        body = _body()
        if fmt not in EXPORT_MIME:
            return jsonify({"error": f"unknown format {fmt!r} "
                                     "(xlsx | sqlite)"}), 400
        keys, cases = body.get("tables"), body.get("cases")
        for lab, v in (("tables", keys), ("cases", cases)):
            if v is not None and (not isinstance(v, list) or not all(
                    isinstance(x, str) for x in v)):
                return jsonify({"error": f"{lab} must be a list of names"}), 400
        if keys is not None:
            bad = [k for k in keys if k not in _tables.TABLES]
            if bad:
                return jsonify({"error": f"unknown table {bad[0]!r}"}), 404
            if not keys:
                return jsonify({"error": "select at least one table"}), 400
        units = body.get("units") or "kN-m"
        model = state["model"]
        try:
            res, ctx = _results(model, body)
            fn = (RX.export_results_xlsx if fmt == "xlsx"
                  else RX.export_results_sqlite)
            data, rep = fn(model, res, ctx, keys=keys, cases=cases,
                           units=units,
                           skip_empty=bool(body.get("skip_empty")))
        except (ValueError, KeyError, TypeError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "mime": EXPORT_MIME[fmt],
                        "filename": _fname(model, f"_results.{fmt}"),
                        "content_b64": base64.b64encode(data).decode("ascii"),
                        "report": rep})

    @app.post("/api/report/docx")
    def report_docx():
        from skyframe.io.docx import MIME, build_docx
        body = _body()
        try:
            data = build_docx(body)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"ok": True, "mime": MIME,
                        "filename": _fname(state["model"], "_report.docx"),
                        "content_b64": base64.b64encode(data).decode("ascii"),
                        "size": len(data)})
