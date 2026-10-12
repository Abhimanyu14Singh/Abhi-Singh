"""HTTP endpoints of BucklingFEM (:mod:`skyframe.core.member_buckling`),
registered by ``create_app`` with one line (CONTRACT "Buckling with shells
and links, sparse eigen and BucklingFEM").

``POST /api/member_buckling`` — body::

    {"member": uid, "model"?: model dict (else the server model),
     "forces"?: {P, M3i, M3j, M2i, M2j, w2},
     "case"?: static case name -> forces from its member stations
              (explicit "forces" keys override),
     "boundary"?: "fork" | "fixed", "mesh"?: {n_long, n_web, n_flange},
     "num_modes"?: 1..20, "section"?: {type, dims},
     "load_position"?: "shear_center" | "top" | "bottom",
     "length"?: m}

-> the :func:`member_buckling` dict; 400 ``{error}`` on bad input.

``POST /api/member_buckling/forces`` — ``{member, case, model?}`` ->
``{forces}`` (the prefill the dialog shows).
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

_ERRS = (ValueError, TypeError, KeyError, RuntimeError, ArithmeticError)


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import member_buckling as _mb

    def _model(body):
        if isinstance(body.get("model"), dict):
            from skyframe.core.model import BuildingModel
            return BuildingModel.from_dict(body["model"])
        return state["model"]

    def _case_forces(model, uid, case):
        from skyframe.engine.opensees_engine import OpenSeesEngine
        if case not in model.cases:
            raise ValueError(f"unknown static case {case!r}")
        m = {x.uid: x for x in model.members}.get(uid)
        if m is None:
            raise ValueError(f"unknown frame member {uid!r}")
        cr = OpenSeesEngine(model).run_static(case)
        return _mb.forces_from_case({"member_stations": cr.member_stations},
                                    uid, m.length)

    @app.post("/api/member_buckling/forces")
    def member_buckling_forces():
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        try:
            model = _model(body)
            f = _case_forces(model, str(body.get("member") or ""),
                             str(body.get("case") or ""))
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"forces": f})

    @app.post("/api/member_buckling")
    def member_buckling():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        try:
            model = _model(body)
            uid = str(body.get("member") or "")
            forces = {}
            if body.get("case"):
                forces = _case_forces(model, uid, str(body["case"]))
            extra = body.get("forces") or {}
            if not isinstance(extra, dict):
                raise ValueError("'forces' must be an object")
            forces.update(extra)
            out = _mb.member_buckling(
                model, uid, forces=forces,
                boundary=str(body.get("boundary") or "fork"),
                mesh=body.get("mesh") or None,
                num_modes=body.get("num_modes", 4),
                section=body.get("section") or None,
                load_position=str(body.get("load_position")
                                  or "shear_center"),
                length=body.get("length"))
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        if body.get("case"):
            out["case"] = str(body["case"])
        return jsonify(out)
