"""HTTP endpoint of the in-house exact section solver
(:mod:`skyframe.core.section_fem`, v1.22); registered by ``create_app``
with one line (CONTRACT "Exact section solver").

``POST /api/sections/exact`` computes WITHOUT touching the server model:

* ``{kind: "shape", type, dims}`` -> ``{kind, method: "exact", props}``
  where ``props`` = :func:`skyframe.core.shape_props.shape_properties`
  with ``torsion="exact"``;
* ``{kind: "builtup", section, materials? | model?}`` -> the built-up
  :func:`~skyframe.core.builtup.section_properties` with the section's
  torsion forced to "exact";
* ``{kind: "designer", section, materials? | model?}`` -> the designer
  :func:`~skyframe.core.sections_designer.section_properties` with
  torsion "exact";
* ``{kind: "regions", regions: [{outer, holes?, n?}], nu?, target?}`` ->
  the raw :func:`skyframe.core.section_fem.solve` dict.

Materials come from ``materials`` (name -> material dict), else
``model`` (a model dict), else the server model.  400 with ``{error}``
on invalid input / unmeshable geometry.  ``GET /api/sections/exact/info``
-> ``{solver: "section_fem", element: "Tri6", default_target, cache}``.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

_ERRS = (ValueError, TypeError, KeyError, ArithmeticError)


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import section_fem as _sf

    def _materials(body: dict):
        if isinstance(body.get("materials"), dict):
            from skyframe.core.model import material_from_dict
            return {str(k): material_from_dict(v, str(k))
                    for k, v in body["materials"].items()}
        if isinstance(body.get("model"), dict):
            from skyframe.core.model import BuildingModel
            return BuildingModel.from_dict(body["model"]).materials
        return state["model"].materials

    @app.post("/api/sections/exact")
    def sections_exact():
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        kind = body.get("kind")
        try:
            if kind == "shape":
                from skyframe.core.shape_props import shape_properties
                dims = body.get("dims")
                props = shape_properties(str(body.get("type") or ""),
                                         dims if isinstance(dims, dict)
                                         else {}, "exact")
                return jsonify({"kind": kind, "method": props["J_method"],
                                "props": props})
            if kind == "builtup":
                from skyframe.core import builtup as _bu
                bs = _bu.BuiltupSection.from_dict(body.get("section") or {})
                bs.torsion = "exact"
                _bu.resolve_components(bs)
                props = _bu.section_properties(bs, _materials(body))
                return jsonify({"kind": kind, "method": props["J_method"],
                                "props": props})
            if kind == "designer":
                from skyframe.core import sections_designer as _sd
                ds = _sd.DesignerSection.from_dict(body.get("section") or {})
                ds.torsion = "exact"
                props = _sd.section_properties(ds, _materials(body))
                return jsonify({"kind": kind, "method": props["J_method"],
                                "props": props})
            if kind == "regions":
                kw = {}
                if body.get("nu") is not None:
                    kw["nu"] = body["nu"]
                if body.get("target") is not None:
                    kw["target"] = int(body["target"])
                res = _sf.solve(body.get("regions"), **kw)
                return jsonify({"kind": kind, "method": "exact",
                                "props": res})
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"error": "kind must be 'shape', 'builtup', "
                                 "'designer' or 'regions'"}), 400

    @app.get("/api/sections/exact/info")
    def sections_exact_info():
        return jsonify({"solver": "section_fem", "element": "Tri6",
                        "default_target": _sf.DEFAULT_TARGET,
                        "cache": _sf.cache_info()})
