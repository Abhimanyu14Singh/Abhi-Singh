"""Wave F HTTP endpoint: model-level Assign on a selection with a story
scope (registered by ``create_app`` with one line; CONTRACT "Wave F —
select and assign in every view").

* ``POST /api/assign`` ``{selection, assign, scope? = "one", model?,
  dry_run?}`` -> ``{model, summary}``.  ``selection`` is the /api/edit shape
  ``{members, shells, links, points}``; ``assign`` holds exactly one of
  ``frame_section`` / ``frame_releases`` / ``shell_section`` /
  ``restraints`` / ``link_property``; ``scope`` "one" | "similar" | "all"
  also writes the corresponding objects of the other stories of the scope
  (:mod:`skyframe.core.assign_scope`).  The working model is replaced unless
  ``dry_run``; ``model`` (optional) assigns on that model instead of the
  working one.  400 with ``{"error"}`` on bad input.
"""

from __future__ import annotations

from flask import jsonify, request


def register(app, state) -> None:
    from skyframe.core.model import BuildingModel
    from skyframe.core.assign_scope import assign as _assign

    @app.post("/api/assign")
    def assign_selection():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        try:
            base = (BuildingModel.from_dict(body["model"])
                    if body.get("model") is not None else state["model"])
            d, summary = _assign(base.to_dict(), body.get("selection"),
                                 body.get("assign"), body.get("scope") or "one")
            model = BuildingModel.from_dict(d)
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        if not body.get("dry_run"):
            state["model"] = model
        return jsonify({"model": model.to_dict(), "summary": summary})
