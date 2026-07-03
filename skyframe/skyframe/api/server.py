"""SkyFrame HTTP API (Flask).

Endpoints per ``CONTRACT.md``:

* ``GET  /``                — SPA (``skyframe/api/web/index.html`` if present)
* ``GET  /static/<path>``   — static assets from the same web dir
* ``GET  /api/health``      — liveness + OpenSees availability
* ``GET  /api/model``       — current model as a dict
* ``POST /api/model/quick`` — regenerate the model via ``quick_building``
* ``POST /api/analyze``     — run the OpenSees engine on the current model

The server keeps one current :class:`BuildingModel` in module-level state
(default: ``quick_building()``).  ``python -m skyframe.api.server`` serves
on 127.0.0.1:8600.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

from skyframe.core.builder import quick_building
from skyframe.core.model import BuildingModel

try:
    from skyframe.engine.opensees_engine import OpenSeesEngine
    _OPENSEES_OK = True
except Exception:  # pragma: no cover - opensees missing/broken
    OpenSeesEngine = None  # type: ignore[assignment]
    _OPENSEES_OK = False

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# module-level singleton: the one current model
_state: Dict[str, BuildingModel] = {"model": quick_building()}

# quick_building kwargs whitelist: name -> (kind, min, max)
_QUICK_PARAMS: Dict[str, tuple] = {
    "bays_x": ("int", 1, 10),
    "bays_y": ("int", 1, 10),
    "stories": ("int", 1, 30),
    "bay_width_x": ("float", 2.0, 15.0),
    "bay_width_y": ("float", 2.0, 15.0),
    "story_height": ("float", 2.0, 6.0),
    "E": ("float", 1.0e6, 5.0e8),
    "column_size": ("float", 0.2, 2.0),
    "beam_b": ("float", 0.15, 1.5),
    "beam_h": ("float", 0.2, 2.0),
    "dead_udl": ("float", 0.0, 500.0),
    "live_udl": ("float", 0.0, 500.0),
    "quake_coeff": ("float", 0.0, 1.0),
    "name": ("str", None, None),
    "base_fixity": ("choice", ("fixed", "pinned"), None),
}


def _validate_quick_kwargs(body: Dict[str, Any]) -> Dict[str, Any]:
    """Whitelist + type-check + clamp quick_building kwargs.

    Raises ``ValueError`` with a user-facing message on bad input.
    """
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    kwargs: Dict[str, Any] = {}
    for key, value in body.items():
        if key not in _QUICK_PARAMS:
            raise ValueError(f"Unknown parameter {key!r}")
        kind, lo, hi = _QUICK_PARAMS[key]
        if kind == "str":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key!r} must be a non-empty string")
            kwargs[key] = value.strip()[:100]
        elif kind == "choice":
            if value not in lo:
                raise ValueError(f"{key!r} must be one of {sorted(lo)}")
            kwargs[key] = value
        else:  # int / float
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key!r} must be a number")
            if kind == "int":
                if float(value) != int(value):
                    raise ValueError(f"{key!r} must be an integer")
                kwargs[key] = max(int(lo), min(int(hi), int(value)))
            else:
                kwargs[key] = max(float(lo), min(float(hi), float(value)))
    return kwargs


def create_app() -> Flask:
    """Flask application factory."""
    app = Flask(__name__, static_folder=None)

    @app.get("/")
    def index():
        index_path = os.path.join(WEB_DIR, "index.html")
        if os.path.isfile(index_path):
            return send_from_directory(WEB_DIR, "index.html")
        return "SkyFrame API running", 200

    @app.get("/static/<path:filename>")
    def static_files(filename: str):
        return send_from_directory(WEB_DIR, filename)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "opensees": _OPENSEES_OK})

    @app.get("/api/model")
    def get_model():
        return jsonify(_state["model"].to_dict())

    @app.post("/api/model/quick")
    def quick_model():
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        try:
            kwargs = _validate_quick_kwargs(body)
            _state["model"] = quick_building(**kwargs)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    @app.post("/api/analyze")
    def analyze():
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        try:
            results = OpenSeesEngine(_state["model"]).run()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(results.to_dict())

    return app


def main() -> None:
    create_app().run(host="127.0.0.1", port=8600)


if __name__ == "__main__":
    main()
