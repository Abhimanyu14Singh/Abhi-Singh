"""HTTP endpoints of built-up sections (:mod:`skyframe.core.builtup`);
registered by ``create_app`` with one line (CONTRACT "Built-up sections").

* ``POST /api/sections/builtup`` ``{action: "upsert"|"delete"|"properties",
  section?, name?, materials?, model?}`` — upsert stores the section + its
  mirrored FrameSection in the server model and returns ``{model,
  properties, section}``; delete returns ``{model}``; properties computes
  WITHOUT touching the server model (materials from ``materials`` (a
  name -> material dict map) or ``model`` when given, else the server
  model) and returns ``{properties, section}``.
* ``GET  /api/sections/builtup/templates`` -> ``{templates: [{id, label,
  params}], fem: bool, db_source: "steel_db"|"stub"}``.
* ``POST /api/sections/builtup/template`` ``{id, params?, name?,
  material?, torsion?="exact"}`` -> ``{section, properties|null}`` (v1.22:
  a NEW template section uses the exact torsion solver unless
  ``torsion`` says otherwise).
* ``GET  /api/sections/builtup/db/standards`` | ``.../db/families?standard=``
  | ``.../db/shapes?standard=&family=&q=&limit=`` — the shape picker
  (``steel_db`` when installed, else the local stub).
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

from skyframe.core.model import BuildingModel

_ERRS = (ValueError, TypeError, KeyError, ArithmeticError)


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import builtup as _bu
    from skyframe.core import builtup_db as _bdb

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    @app.post("/api/sections/builtup")
    def sections_builtup():
        body = _body()
        action = body.get("action")
        model = state["model"]
        if action == "properties":
            try:
                if isinstance(body.get("materials"), dict):
                    from skyframe.core.model import material_from_dict
                    mats = {str(k): material_from_dict(v, str(k))
                            for k, v in body["materials"].items()}
                elif isinstance(body.get("model"), dict):
                    mats = BuildingModel.from_dict(body["model"]).materials
                else:
                    mats = model.materials
                bs = _bu.BuiltupSection.from_dict(body.get("section") or {})
                _bu.resolve_components(bs)
                props = _bu.section_properties(bs, mats)
            except _ERRS as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({"properties": props, "section": bs.to_dict()})
        if action == "upsert":
            try:
                bs = _bu.BuiltupSection.from_dict(body.get("section") or {})
                props = model.add_builtup_section(bs)
            except _ERRS as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({"model": model.to_dict(), "properties": props,
                            "section": bs.to_dict()})
        if action == "delete":
            name = body.get("name") or (body.get("section") or {}).get("name")
            try:
                model.remove_builtup_section(str(name))
            except KeyError:
                return jsonify({"error": f"unknown built-up section "
                                         f"{name!r}"}), 400
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({"model": model.to_dict()})
        return jsonify({"error": "'action' must be 'upsert', 'delete' or "
                                 "'properties'"}), 400

    @app.get("/api/sections/builtup/templates")
    def sections_builtup_templates():
        return jsonify({"templates": _bu.template_list(),
                        "fem": _bu.fem_available(),
                        "db_source": _bdb.SOURCE})

    @app.post("/api/sections/builtup/template")
    def sections_builtup_template():
        body = _body()
        model = state["model"]
        try:
            params = body.get("params") or {}
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            bs = _bu.template_section(str(body.get("id")), params,
                                      name=str(body.get("name") or "BU1"),
                                      material=str(body.get("material")
                                                   or ""),
                                      torsion=str(body.get("torsion")
                                                  or "exact"))
            props = (_bu.section_properties(bs, model.materials)
                     if bs.material in model.materials else None)
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"section": bs.to_dict(), "properties": props})

    @app.get("/api/sections/builtup/db/standards")
    def sections_builtup_db_standards():
        return jsonify({"standards": _bdb.standards(),
                        "source": _bdb.SOURCE})

    @app.get("/api/sections/builtup/db/families")
    def sections_builtup_db_families():
        try:
            return jsonify({"families": _bdb.families(
                request.args.get("standard", "aisc"))})
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/sections/builtup/db/shapes")
    def sections_builtup_db_shapes():
        a = request.args
        try:
            lim = int(a.get("limit", 200))
            rows = _bdb.shapes(a.get("standard", "aisc"),
                               family=a.get("family") or None,
                               query=a.get("q") or None)
        except _ERRS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"shapes": rows[:max(lim, 1)], "total": len(rows)})
