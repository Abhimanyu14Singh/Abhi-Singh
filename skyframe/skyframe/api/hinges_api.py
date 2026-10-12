"""HTTP endpoints of the Wave B hinge features; registered by
``create_app`` with one line (CONTRACT "Auto hinges from code tables").

* ``GET  /api/hinges/catalog`` -> ``{codes: {code: {component: {dofs,
  table, clause, verified, note}}}, params: {defaults}, brace_shapes,
  hinge_types, interaction_types, wall_defaults}``.
* ``POST /api/hinges/auto-preview`` ``{property: <auto hinge property>,
  members: [uid, ...], P_G?: kN (compression +), model?: <model dict>}``
  -> ``{previews: [{uid, P_G, reason, hinges: [{type, property,
  acceptance_negative, thy, meta}]}]}`` — the generated backbones at an
  explicit axial load, WITHOUT running an analysis (the run uses the
  gravity case).  400 on an invalid property / unknown member.
* ``POST /api/hinges/surface-preview`` ``{property: <PMM|M2M3>, P?: kN
  (ETABS sign: compression negative)}`` -> ``{M2y, M3y, P, fit}``.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

_ERRS = (ValueError, TypeError, KeyError, ArithmeticError)


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import interaction_hinges as _cih
    from skyframe.core import user_hinges as _cu
    from skyframe.core import wall_hinges as _cwh
    from skyframe.design import hinge_tables as _ht

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    @app.get("/api/hinges/catalog")
    def hinges_catalog():
        out = _ht.catalog()
        out["hinge_types"] = list(_cu.HINGE_TYPES)
        out["interaction_types"] = list(_cih.INTERACTION_TYPES)
        out["wall_defaults"] = dict(_cwh.WALL_KEYS)
        return jsonify(out)

    @app.post("/api/hinges/auto-preview")
    def hinges_auto_preview():
        body = _body()
        try:
            from skyframe.core.model import BuildingModel
            from skyframe.engine.auto_hinges import preview
            model = (BuildingModel.from_dict(body["model"])
                     if isinstance(body.get("model"), dict)
                     else state["model"])
            prop = _cu.normalize_property("preview", body.get("property"))
            if prop["type"] != "auto":
                raise ValueError("property must be an auto hinge")
            P_G = body.get("P_G", 0.0)
            if isinstance(P_G, bool) or not isinstance(P_G, (int, float)):
                raise ValueError("P_G must be a number")
            uids = body.get("members") or []
            if not isinstance(uids, list):
                raise ValueError("members must be a list of uids")
            by = {m.uid: m for m in model.members}
            out = []
            for uid in uids:
                if uid not in by:
                    raise ValueError(f"unknown member {uid!r}")
                out.append(preview(model, by[uid], prop, float(P_G)))
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"previews": out})

    @app.post("/api/hinges/surface-preview")
    def hinges_surface_preview():
        body = _body()
        try:
            prop = _cu.normalize_property("preview", body.get("property"))
            if prop["type"] not in _cih.INTERACTION_TYPES:
                raise ValueError("property must be a PMM or M2M3 hinge")
            P = body.get("P", 0.0)
            if isinstance(P, bool) or not isinstance(P, (int, float)):
                raise ValueError("P must be a number")
            res = _cih.ellipse_at(prop["surface"], float(P))
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(res)
