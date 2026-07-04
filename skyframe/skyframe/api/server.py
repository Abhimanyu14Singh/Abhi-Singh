"""SkyFrame HTTP API (Flask).

Endpoints per ``CONTRACT.md``:

* ``GET  /``                — SPA (``skyframe/api/web/index.html`` if present)
* ``GET  /static/<path>``   — static assets from the same web dir
* ``GET  /api/health``      — liveness + OpenSees availability
* ``GET  /api/model``       — current model as a dict
* ``POST /api/model``       — replace the model (``BuildingModel.from_dict``)
* ``POST /api/model/quick`` — regenerate the model via ``quick_building``
* ``POST /api/analyze``     — run the OpenSees engine on the current model

v0.3 additions:

* ``GET    /api/models``             — saved-model listing (name/mtime/counts)
* ``POST   /api/models/<name>``      — save the current model to disk
* ``POST   /api/models/<name>/open`` — load a saved model as the current one
* ``DELETE /api/models/<name>``      — delete a saved model
* ``GET    /api/sections/library``   — built-in steel section library (SI)

v0.4 additions:

* ``POST /api/pattern/wind`` — add an auto ASCE 7-style wind LoadPattern to
  the current model (body: ``{name, direction, V, exposure, Cp,
  importance}``; ``V`` required, m/s) and return the updated model dict.

v0.5 additions (no new endpoints — the existing model/analyze round trip
carries every new field, see CONTRACT.md "v0.5 additions"):

* ``POST /api/model`` accepts/echoes shell-region ``openings`` (rectangular
  holes in region-parametric u/v fractions), ``pushover_cases`` (nonlinear
  static pushover definitions), the ``diaphragm``/``story_diaphragm``
  rigid|none options, and ``links`` (6-DOF spring elements);
* ``POST /api/analyze`` results gain a ``"pushover"`` block per pushover
  case: ``{roof_disp, base_shear, roof_drift, hinge_rotations, warnings}``
  (skipped with a top-level ``"warning"`` when the combined pushover steps
  exceed 2000).

Saved models live as ``<name>.skyframe.json`` files in ``~/.skyframe/models``
(override with the ``SKYFRAME_MODELS_DIR`` environment variable; the
directory is created on demand).  Names must match ``[A-Za-z0-9 _-]{1,60}``.

The server keeps one current :class:`BuildingModel` in module-level state
(default: ``quick_building()``).  ``python -m skyframe.api.server`` serves
on 127.0.0.1:8600.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

from flask import Flask, jsonify, request, send_from_directory

from skyframe.core.builder import make_wind_pattern, quick_building
from skyframe.core.model import BuildingModel
from skyframe.core.sections_library import library_to_dict

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


_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,60}$")
_MODEL_SUFFIX = ".skyframe.json"


def _models_dir() -> str:
    """Saved-models directory (created on demand).

    ``SKYFRAME_MODELS_DIR`` overrides the default ``~/.skyframe/models``.
    Read per-request so tests (and users) can repoint it via the env var.
    """
    path = os.environ.get("SKYFRAME_MODELS_DIR") or os.path.join(
        os.path.expanduser("~"), ".skyframe", "models")
    os.makedirs(path, exist_ok=True)
    return path


def _model_path(name: str) -> str:
    return os.path.join(_models_dir(), name + _MODEL_SUFFIX)


def _model_entry(name: str, path: str) -> Dict[str, Any]:
    """Cheap listing entry: name, mtime, story & member counts."""
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    return {"name": name,
            "mtime": os.path.getmtime(path),
            "stories": len(d.get("stories") or []),
            "members": len(d.get("members") or [])}


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

    @app.post("/api/model")
    def set_model():
        body = request.get_json(silent=True)
        if body is None:
            return jsonify({"error": "Request body must be a JSON object"}), 400
        try:
            model = BuildingModel.from_dict(body)
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        _state["model"] = model
        return jsonify(model.to_dict())

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

    # ------------------------------------------------ v0.3: model save/open
    @app.get("/api/models")
    def list_models():
        entries = []
        for fn in sorted(os.listdir(_models_dir())):
            if not fn.endswith(_MODEL_SUFFIX):
                continue
            name = fn[:-len(_MODEL_SUFFIX)]
            try:
                entries.append(_model_entry(name, _model_path(name)))
            except (OSError, ValueError):
                continue  # unreadable/corrupt file: skip from the gallery
        return jsonify(entries)

    @app.post("/api/models/<name>")
    def save_model(name: str):
        if not _MODEL_NAME_RE.fullmatch(name):
            return jsonify({"error": "Model name must match "
                                     "[A-Za-z0-9 _-]{1,60}"}), 400
        path = _model_path(name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_state["model"].to_dict(), fh)
        return jsonify(_model_entry(name, path))

    @app.post("/api/models/<name>/open")
    def open_model(name: str):
        if not _MODEL_NAME_RE.fullmatch(name):
            return jsonify({"error": "Model name must match "
                                     "[A-Za-z0-9 _-]{1,60}"}), 400
        path = _model_path(name)
        if not os.path.isfile(path):
            return jsonify({"error": f"No saved model named {name!r}"}), 404
        try:
            with open(path, "r", encoding="utf-8") as fh:
                model = BuildingModel.from_dict(json.load(fh))
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": f"Saved model is invalid: {exc}"}), 400
        _state["model"] = model
        return jsonify(model.to_dict())

    @app.delete("/api/models/<name>")
    def delete_model(name: str):
        if not _MODEL_NAME_RE.fullmatch(name):
            return jsonify({"error": "Model name must match "
                                     "[A-Za-z0-9 _-]{1,60}"}), 400
        path = _model_path(name)
        if not os.path.isfile(path):
            return jsonify({"error": f"No saved model named {name!r}"}), 404
        os.remove(path)
        return jsonify({"deleted": name})

    # ------------------------------------------------ v0.3: section library
    @app.get("/api/sections/library")
    def sections_library():
        return jsonify(library_to_dict())

    # --------------------------------------------- v0.4: auto wind pattern
    @app.post("/api/pattern/wind")
    def wind_pattern():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name", "WIND")
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            return jsonify({"error": "'name' must be a non-empty string "
                                     "(max 60 chars)"}), 400
        try:
            v = body.get("V")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("'V' (basic wind speed, m/s) must be a "
                                 "number")
            cp = body.get("Cp", 1.3)
            imp = body.get("importance", 1.0)
            for key, val in (("Cp", cp), ("importance", imp)):
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"{key!r} must be a number")
            make_wind_pattern(
                _state["model"], name.strip(),
                direction=body.get("direction", "X"),
                basic_wind_speed=float(v),
                exposure=body.get("exposure", "C"),
                cp_total=float(cp), importance=float(imp))
        except (ValueError, TypeError) as exc:
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
