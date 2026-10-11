"""HTTP endpoints of the built-in material libraries
(:mod:`skyframe.core.material_db`); registered by ``create_app`` with one
line (CONTRACT "v1.21 material library").

* ``GET  /api/material_db/standards``
* ``GET  /api/material_db/materials?standard=&kind=&q=``
* ``GET  /api/material_db/material?standard=&name=``
* ``POST /api/material_db/import``  ``{standard, names[], on_clash?, rename?}``
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

from skyframe.core import material_db as _mdb


def register(app, state: Dict[str, Any]) -> None:

    def _err(exc, code=400):
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        return jsonify({"error": str(msg)}), code

    @app.get("/api/material_db/standards")
    def material_db_standards():
        return jsonify(_mdb.standards())

    @app.get("/api/material_db/materials")
    def material_db_materials():
        std = request.args.get("standard", "")
        kind = request.args.get("kind") or None
        try:
            return jsonify({"standard": std,
                            "materials": _mdb.listing(std, kind,
                                                      request.args.get("q"))})
        except KeyError as exc:
            return _err(exc, 404)
        except ValueError as exc:
            return _err(exc)

    @app.get("/api/material_db/material")
    def material_db_material():
        std = request.args.get("standard", "")
        name = request.args.get("name", "")
        try:
            rec = _mdb.material(std, name)
            return jsonify({"standard": std, "record": rec,
                            "material": _mdb.record_to_material(
                                rec, None, std).to_dict()})
        except KeyError as exc:
            return _err(exc, 404)

    @app.post("/api/material_db/import")
    def material_db_import():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        model = state["model"]
        try:
            res = _mdb.import_into(model, str(body.get("standard", "")),
                                   body.get("names"),
                                   on_clash=body.get("on_clash", "suffix"),
                                   rename=body.get("rename"))
        except KeyError as exc:
            return _err(exc, 404)
        except ValueError as exc:
            return _err(exc, 409 if "already exists" in str(exc) else 400)
        return jsonify({"imported": res, "model": model.to_dict()})
