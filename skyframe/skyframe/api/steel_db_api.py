"""HTTP endpoints of the steel section databases and the parametric shape
calculator (:mod:`skyframe.core.steel_db`, :mod:`skyframe.core.shape_props`);
registered by ``create_app`` with one line (CONTRACT "Steel section
database and parametric shapes").

* ``GET  /api/steel_db/standards`` -> ``[{id, standard, edition, count,
  source, families}]``
* ``GET  /api/steel_db/shapes?standard=&family=&q=&limit=&raw=`` ->
  ``{standard, family, q, total, count, shapes: [record]}`` (records
  without their verbatim ``aisc`` source block unless ``raw=1``)
* ``POST /api/steel_db/import {standard, names[], material,
  apply?=true}`` -> ``{added, skipped, sections: {name: section dict},
  model?}``; ``apply`` false only builds the section dicts (the UI merges
  them into its draft), true adds them to the current model.
* ``POST /api/steel_db/shape_props {type, dims}`` -> exact parametric
  properties (SI), 400 on invalid dimensions.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request


def _strip(rec: dict, raw: bool) -> dict:
    if not raw:
        rec.pop("aisc", None)
    return rec


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import steel_db as _sdb

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    @app.get("/api/steel_db/standards")
    def steel_db_standards():
        out = []
        for s in _sdb.standards():
            out.append({**s, "families": _sdb.families(s["id"])})
        return jsonify(out)

    @app.get("/api/steel_db/shapes")
    def steel_db_shapes():
        a = request.args
        std = a.get("standard") or "aisc"
        try:
            sid = _sdb.standard_id(std)
            recs = _sdb.shapes(sid, a.get("family") or None,
                               a.get("q") or None)
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'\"")}), 404
        total = len(recs)
        try:
            limit = int(a.get("limit") or 0)
        except ValueError:
            return jsonify({"error": "limit must be an integer"}), 400
        if limit > 0:
            recs = recs[:limit]
        raw = a.get("raw") in ("1", "true", "yes")
        return jsonify({"standard": sid, "family": a.get("family") or None,
                        "q": a.get("q") or "", "total": total,
                        "count": len(recs),
                        "shapes": [_strip(r, raw) for r in recs]})

    @app.post("/api/steel_db/import")
    def steel_db_import():
        b = _body()
        names = b.get("names")
        if isinstance(names, str):
            names = [names]
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) for n in names):
            return jsonify({"error": "names must be a non-empty list of "
                                     "shape names"}), 400
        mat = b.get("material")
        if not isinstance(mat, str) or not mat:
            return jsonify({"error": "material is required"}), 400
        std = b.get("standard") or "aisc"
        apply = b.get("apply", True) is not False
        model = state["model"]
        if apply and mat not in model.materials:
            return jsonify({"error": f"unknown material {mat!r}"}), 400
        try:
            secs = [_sdb.section_from_db(std, n, mat) for n in names]
        except KeyError as exc:
            return jsonify({"error": str(exc).strip("'\"")}), 404
        added, skipped = [], []
        for s in secs:
            if apply and s.name in model.sections:
                skipped.append(s.name)
                continue
            if apply:
                try:
                    model.add_section(s)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
            added.append(s.name)
        out = {"added": added, "skipped": skipped,
               "sections": {s.name: s.to_dict() for s in secs}}
        if apply:
            out["model"] = model.to_dict()
        return jsonify(out)

    @app.post("/api/steel_db/shape_props")
    def steel_db_shape_props():
        from skyframe.core.shape_props import (SHEAR_AREA_FORMULAS,
                                               shape_properties)
        b = _body()
        typ = b.get("type") or b.get("shape")
        dims = b.get("dims")
        try:
            props = shape_properties(typ, dims if isinstance(dims, dict)
                                     else {})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"type": typ, "props": props,
                        "shear_area_formula": SHEAR_AREA_FORMULAS[typ]})
