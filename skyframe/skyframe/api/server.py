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

from skyframe.core.builder import (add_self_weight, make_wind_pattern,
                                   quick_building)
from skyframe.core.codes import (apply_asce7_combinations, asce7_elf,
                                 make_rs_case_from_code)
from skyframe.core.model import BuildingModel, make_notional_pattern
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

    # ------------------------------------------- v0.8: springs + thermal
    @app.post("/api/support/spring")
    def support_spring():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        point = body.get("point")
        stiffness = body.get("stiffness")
        if (not isinstance(point, list) or len(point) != 3
                or not all(isinstance(v, (int, float))
                           and not isinstance(v, bool) for v in point)):
            return jsonify({"error": "'point' must be [x, y, z] numbers"}), 400
        if (not isinstance(stiffness, list) or len(stiffness) != 6
                or not all(isinstance(v, (int, float))
                           and not isinstance(v, bool) for v in stiffness)):
            return jsonify({"error": "'stiffness' must be 6 numbers "
                                     "[kx, ky, kz, krx, kry, krz]"}), 400
        try:
            _state["model"].add_spring_support(
                [float(v) for v in point], [float(v) for v in stiffness])
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    @app.post("/api/pattern/thermal")
    def pattern_thermal():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        pattern = body.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return jsonify({"error": "'pattern' is required"}), 400
        loads = body.get("loads")
        if not isinstance(loads, list) or not loads:
            return jsonify({"error": "'loads' must be a non-empty list of "
                                     "{member_uid, dT}"}), 400
        try:
            for ld in loads:
                if not isinstance(ld, dict):
                    raise ValueError("each thermal load must be an object "
                                     "{member_uid, dT}")
                uid = ld.get("member_uid")
                dT = ld.get("dT")
                if not isinstance(uid, str) or not uid:
                    raise ValueError("thermal load needs a 'member_uid'")
                if isinstance(dT, bool) or not isinstance(dT, (int, float)):
                    raise ValueError("thermal load 'dT' must be a number")
                _state["model"].add_thermal_load(pattern.strip(), uid,
                                                 float(dT))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    # -------------------------------------------- v0.7: self-weight + codes
    def _num(body: Dict[str, Any], key: str, default=None, required=False):
        """Fetch a numeric field (rejects bools), with optional default."""
        if key not in body or body.get(key) is None:
            if required:
                raise ValueError(f"{key!r} is required")
            return default
        v = body[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{key!r} must be a number")
        return float(v)

    @app.post("/api/pattern/selfweight")
    def pattern_selfweight():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name", "SW")
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            return jsonify({"error": "'name' must be a non-empty string "
                                     "(max 60 chars)"}), 400
        try:
            factor = _num(body, "factor", default=1.0)
            add_self_weight(_state["model"], pattern=name.strip(),
                            factor=factor)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    @app.post("/api/combos/asce7")
    def combos_asce7():
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        standard = body.get("standard", "LRFD")
        try:
            apply_asce7_combinations(_state["model"], standard=standard)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    @app.post("/api/case/rs-code")
    def case_rs_code():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return jsonify({"error": "'name' is required"}), 400
        try:
            make_rs_case_from_code(
                _state["model"], name.strip(),
                direction=body.get("direction", "X"),
                Ss=_num(body, "Ss", required=True),
                S1=_num(body, "S1", required=True),
                site_class=body.get("site_class", "D"),
                R=_num(body, "R", default=8.0),
                Ie=_num(body, "Ie", default=1.0))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    @app.post("/api/pattern/elf")
    def pattern_elf():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name", "ELF")
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            return jsonify({"error": "'name' must be a non-empty string "
                                     "(max 60 chars)"}), 400
        try:
            asce7_elf(
                _state["model"],
                SDS=_num(body, "SDS", required=True),
                SD1=_num(body, "SD1", required=True),
                R=_num(body, "R", required=True),
                Ie=_num(body, "Ie", default=1.0),
                direction=body.get("direction", "X"),
                name=name.strip())
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    # ---------------------------------------- v0.10: RS directional + notional
    @app.post("/api/case/rs-directional")
    def case_rs_directional():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name")
        name_x = body.get("name_x")
        name_y = body.get("name_y")
        method = body.get("method", "100_30")
        if not isinstance(name, str) or not name.strip():
            return jsonify({"error": "'name' is required"}), 400
        if not isinstance(name_x, str) or not isinstance(name_y, str):
            return jsonify({"error": "'name_x' and 'name_y' (RS case names) "
                                     "are required"}), 400
        try:
            _state["model"].add_rs_combo(name.strip(), name_x, name_y,
                                         method=method)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    @app.post("/api/pattern/notional")
    def pattern_notional():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name", "NOTIONAL")
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            return jsonify({"error": "'name' must be a non-empty string "
                                     "(max 60 chars)"}), 400
        try:
            coeff = _num(body, "coeff", default=0.002)
            make_notional_pattern(
                _state["model"], name.strip(),
                direction=body.get("direction", "X"),
                coeff=coeff,
                gravity_pattern=body.get("gravity_pattern", "DEAD"))
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

    # --------------------------------------------- v0.6: preliminary design
    @app.post("/api/design/steel")
    def design_steel():
        """Preliminary AISC 360 checks for a case/combo (runs analysis).

        Body: {"case": "<name>", optional "Fy","kx","ky","Lb"}.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.steel import (check_members,
                                           check_members_envelope, summarize)
        body = request.get_json(silent=True) or {}
        case = body.get("case")
        combos = body.get("combos")     # v0.9: True or ["name", ...]
        if not combos and (not isinstance(case, str) or not case):
            return jsonify({"error": "'case' (name of a case/combo) or "
                                     "'combos' is required"}), 400
        kw = {k: float(body[k]) for k in ("Fy", "kx", "ky", "Lb")
              if isinstance(body.get(k), (int, float))}
        try:
            results = OpenSeesEngine(_state["model"]).run()
            if combos:
                names = combos if isinstance(combos, list) else None
                checks = check_members_envelope(
                    _state["model"], results, combos=names, **kw)
            else:
                checks = check_members(_state["model"], results, case, **kw)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"preliminary": True, "case": case, "combos": combos,
                        "checks": [c.to_dict() for c in checks],
                        "summary": summarize(checks)})

    @app.post("/api/design/concrete")
    def design_concrete():
        """Preliminary ACI 318 checks for a case/combo (runs analysis).

        Body: {"case": "<name>", "rebar": {uid: RebarLayout fields},
               optional "fc"}.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.concrete import (
            RebarLayout, check_concrete_members,
            check_concrete_members_envelope)
        from skyframe.design.concrete import summarize as summ_c
        body = request.get_json(silent=True) or {}
        case = body.get("case")
        combos = body.get("combos")     # v0.9: True or ["name", ...]
        if not combos and (not isinstance(case, str) or not case):
            return jsonify({"error": "'case' or 'combos' is required"}), 400
        raw = body.get("rebar")
        if not isinstance(raw, dict):
            return jsonify({"error": "'rebar' must be a {uid: layout} "
                                     "object"}), 400
        try:
            rebar = {uid: RebarLayout(**fields) for uid, fields in raw.items()}
            kw = {"fc": float(body["fc"])} if isinstance(
                body.get("fc"), (int, float)) else {}
            results = OpenSeesEngine(_state["model"]).run()
            if combos:
                names = combos if isinstance(combos, list) else None
                checks = check_concrete_members_envelope(
                    _state["model"], results, rebar, combos=names, **kw)
            else:
                checks = check_concrete_members(
                    _state["model"], results, case, rebar, **kw)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"preliminary": True, "case": case, "combos": combos,
                        "checks": [c.to_dict() for c in checks],
                        "summary": summ_c(checks)})

    # --------------------------------------------- v0.6: model importers
    @app.post("/api/import/<fmt>")
    def import_model(fmt: str):
        """Import a model from DXF / e2k / IFC text and make it current.

        Body: {"text": "<file contents>", plus DXF needs
        "stories":[h,...], "column_section","beam_section"}.
        Response: {"model": <model dict>, "warnings": [...]}.
        """
        body = request.get_json(silent=True) or {}
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "'text' (file contents) is "
                                     "required"}), 400
        try:
            if fmt == "dxf":
                from skyframe.io.dxf import import_dxf
                stories = body.get("stories")
                if not isinstance(stories, list) or not stories:
                    return jsonify({"error": "DXF import needs a non-empty "
                                             "'stories' height list"}), 400
                model, warnings = import_dxf(
                    text, stories=[float(h) for h in stories],
                    column_section=body.get("column_section", "DXF-COL"),
                    beam_section=body.get("beam_section", "DXF-BEAM"),
                    wall_section=body.get("wall_section"),
                    unit_scale=float(body.get("unit_scale", 1.0)))
            elif fmt == "e2k":
                from skyframe.io.e2k import import_e2k
                model, warnings = import_e2k(text)
            elif fmt == "ifc":
                from skyframe.io.ifc import import_ifc
                model, warnings = import_ifc(text)
            else:
                return jsonify({"error": f"unknown format {fmt!r} "
                                         "(dxf|e2k|ifc)"}), 400
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        _state["model"] = model
        return jsonify({"model": model.to_dict(), "warnings": warnings})

    return app


def main() -> None:
    create_app().run(host="127.0.0.1", port=8600)


if __name__ == "__main__":
    main()
