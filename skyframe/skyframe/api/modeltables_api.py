"""HTTP endpoints of the Interactive Database Editing tables
(:mod:`skyframe.core.modeltables`); registered by ``create_app`` with one
line (CONTRACT "Interactive database editing (model tables)").

Every endpoint takes an optional ``model`` (a ``to_dict()`` JSON object;
default: the server's current model).  A successful apply / import replaces
the server's current model unless ``dry_run`` is true.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict

from flask import Response, jsonify, request

from skyframe.core import modeltables as MT
from skyframe.core.model import BuildingModel


def register(app, state: Dict[str, Any]) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _base(body: dict) -> dict:
        if body.get("model") is not None:
            return BuildingModel.from_dict(body["model"]).to_dict()
        return state["model"].to_dict()

    def _bad(msg, errors=None, code=400):
        out = {"error": msg}
        if errors is not None:
            out["errors"] = errors
        return jsonify(out), code

    def _result(model, errors, summary, body):
        if errors:
            n = len(errors)
            first = errors[0]
            where = ""
            if first.get("table"):
                where = f" [{first['table']}"
                if first.get("row") is not None:
                    where += f" row {first['row'] + 1}"
                if first.get("col"):
                    where += f", {first['col']}"
                where += "]"
            msg = (f"{n} error{'s' if n != 1 else ''}: "
                   f"{first['message']}{where}")
            if body.get("soft_errors"):
                # 200 + ok:false: browsers log every 4xx as a console error
                return jsonify({"ok": False, "error": msg, "errors": errors})
            return _bad(msg, errors)
        if not body.get("dry_run"):
            state["model"] = model
        return jsonify({"ok": True, "model": model.to_dict(),
                        "summary": summary})

    @app.route("/api/modeltables/list", methods=["GET", "POST"])
    def modeltables_list():
        body = _body()
        try:
            d = _base(body)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        return jsonify({"tables": MT.list_tables(d),
                        "quantities": MT.QUANTITIES})

    @app.post("/api/modeltables/apply")
    def modeltables_apply_many():
        body = _body()
        try:
            d = _base(body)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        tables = body.get("tables")
        if not isinstance(tables, dict):
            return _bad("'tables' must be an object {table key: rows}")
        return _result(*MT.apply_tables(d, tables), body)

    @app.post("/api/modeltables/export")
    def modeltables_export():
        body = _body()
        try:
            d = _base(body)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        data = MT.export_zip(d)
        if body.get("as_base64"):
            return jsonify({"filename": "model_tables.zip",
                            "zip_b64": base64.b64encode(data).decode()})
        return Response(data, mimetype="application/zip", headers={
            "Content-Disposition": 'attachment; filename="model_tables.zip"'})

    @app.post("/api/modeltables/import")
    def modeltables_import():
        if request.mimetype in ("application/zip", "application/octet-stream"):
            body, data = dict(request.args), request.get_data()
        else:
            body = _body()
            raw = body.get("zip_b64")
            if not isinstance(raw, str):
                return _bad("send the zip as the request body "
                            "(application/zip) or as {zip_b64}")
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                return _bad("zip_b64 is not valid base64")
        try:
            d = _base(body)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        return _result(*MT.import_zip(d, data), body)

    @app.post("/api/modeltables/<key>")
    def modeltables_get(key: str):
        if key not in MT.table_keys():
            return _bad(f"unknown table {key!r}", code=404)
        try:
            d = _base(_body())
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        return jsonify(MT.get_table(d, key))

    @app.post("/api/modeltables/<key>/apply")
    def modeltables_apply(key: str):
        if key not in MT.table_keys():
            return _bad(f"unknown table {key!r}", code=404)
        body = _body()
        rows = body.get("rows")
        if not isinstance(rows, list):
            return _bad("'rows' must be a list")
        try:
            d = _base(body)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        return _result(*MT.apply_tables(d, {key: rows}), body)

    @app.post("/api/modeltables/<key>/csv")
    def modeltables_csv(key: str):
        if key not in MT.table_keys():
            return _bad(f"unknown table {key!r}", code=404)
        body = _body()
        try:
            d = _base(body)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        return jsonify({"filename": f"{key}.csv",
                        "csv": MT.table_to_csv(
                            d, key, include_id=bool(body.get("include_id",
                                                             True)))})

    @app.post("/api/modeltables/<key>/csv_import")
    def modeltables_csv_import(key: str):
        if key not in MT.table_keys():
            return _bad(f"unknown table {key!r}", code=404)
        body = _body()
        text = body.get("csv")
        if not isinstance(text, str):
            return _bad("'csv' (text) is required")
        try:
            d = _base(body)
            rows = MT.csv_to_rows(d, key, text)
        except (ValueError, KeyError, TypeError) as exc:
            return _bad(str(exc))
        if not body.get("apply"):
            return jsonify({"rows": rows})
        return _result(*MT.apply_tables(d, {key: rows}), body)
