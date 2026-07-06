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

v0.15 additions (no new endpoints):

* ``POST /api/model`` round-trips the LinkMember ``link_type``/``params``
  fields (advanced device links: damper / gap / hook / isolator — 400 on a
  bad ``link_type`` or incomplete/unknown ``params``), the ShellRegion
  ``pier`` label, and ``auto_pier_walls``;
* ``POST /api/analyze`` results carry the ``"piers"`` block automatically
  (per static case + additive combo -> pier label -> story -> {P, V, M}).

v0.16 additions:

* ``GET /api/live-reduction`` — ASCE 7-16 §4.7 reduced-live-load multipliers
  per column (pure model geometry, no analysis);
* ``POST /api/design/steel`` / ``POST /api/design/concrete`` accept optional
  ``live_reduction: true`` + ``live_case`` ("LIVE") — the live-attributable
  share of each column's demands is scaled by its §4.7 multiplier R before
  checking (design-stage reduction; analysis results are untouched), and the
  response carries the ``live_reduction`` factors;
* ``POST /api/analyze`` results gain ``member_deflections`` per static
  case/additive combo and the top-level ``deflection_checks`` block;
  ``POST /api/model`` round-trips ``model.deflection_limit``.

v0.17 additions:

* ``POST /api/combos/asce7`` accepts optional ``SDS`` (>= 0): the vertical
  seismic component Ev = 0.2*SDS*D enters the seismic combinations per
  ASCE 7-16 §12.4.2.3 ((1.2+0.2*SDS)D / (0.9-0.2*SDS)D, ASD analogues);
* ``POST /api/model`` round-trips ``model.panel_zones``
  ("none" | "rigid" | "scissors"; 400 on any other value).

v0.18 additions:

* ``POST /api/design/wall`` — ACI 318 uniform-reinforcing shear wall pier
  checks driven by the v0.15 per-story pier P/V/M forces (body:
  ``{combos?: [names] (default: all additive combos), rho_v?, rho_h?, fy?,
  fc_prime?}``); per-pier-per-story PMM (strip-integrated interaction),
  §11.5.4.3 shear, and the §18.10.6.3 boundary-element trigger.  400 on bad
  parameters or when the model has no pier forces (label walls or set
  ``auto_pier_walls``);
* ``POST /api/design/punching`` — ACI two-way (punching) shear checks at
  every column supporting a meshed shell slab (body: ``{case?: name
  (default: the first DEAD-classified case), fc_prime?, cover?}``); 400 on
  an unknown case, EMPTY ``columns`` list (not an error) when the model
  has no shell slabs;
* ``POST /api/results/virtual-work`` — per-member unit-load virtual-work
  contributions to the roof displacement for a linear static case (body:
  ``{case: name, direction: "X"|"Y"}``); response ``{contributions:
  {uid: e}, total, roof_disp, case, direction}``; 400 on an unknown case /
  bad direction / nonlinear or P-Delta case.

v0.19 additions:

* ``POST /api/model`` round-trips ``FrameMember.hinges`` ("none" |
  "auto_m3"; 400 on any other value) and
  ``PushoverCase.hinges == "asce41"`` + ``hinge_params``;
* ``POST /api/analyze`` pushover blocks gain ``hinges`` (asce41 mode
  only): per hinge ``{uid, end, My, thy, a, b, c, IO, LS, CP, kind,
  rot: [per step], moment: [per step], state: [per step]}`` with state in
  elastic | IO | LS | CP | collapse;
* ``POST /api/results/performance-point`` — ASCE 41-17 §7.4.3 coefficient
  method (body ``{case: pushover name, SDS, SD1, site_class?, W?}``):
  bilinearizes the capacity curve (§7.4.3.2.4 equal-area / 0.6Vy secant),
  ``Te = Ti sqrt(Ki/Ke)`` with Ti the dominant modal period in the push
  direction, W defaults to the seismic weight (story + nodal masses * g);
  response carries Ki/Ke/Vy/dy/Te/Sa/mu/C0/C1/C2/delta_t, the nearest
  pushover ``step`` index and that step's ``hinge_summary`` state counts;
* ``POST /api/loads/pattern-live`` — ETABS-style skip live loading (body
  ``{live_pattern?: name (default: the live-classified pattern)}``):
  creates ``<live>__ODD`` / ``<live>__EVEN`` patterns by continuous-run
  span parity, factored cases ``PLL_ALL/ODD/EVEN`` (1.2D + 1.6L) and the
  ``PATTERN-LL`` envelope combo; returns the model dict;
* ``POST /api/case/auto-sequence`` — one-click staged-construction case
  (body ``{name?, pattern?, include_live?}``, defaults SEQ / the dead
  pattern): wraps ``add_staged_case`` (per-story sequential gravity);
  returns the model dict.

v0.20 additions:

* ``POST /api/design/composite`` — AISC 360-16 I3 composite beam checks
  for every horizontal W-shape beam supporting a meshed shell slab (body:
  ``{combos?: [names] (default: all additive combos), fc_prime?, t_slab?,
  hr?, stud_d?, stud_Fu?, rib_spacing?, shored?: bool}``); response
  ``{preliminary, combos, params, beams: [CompositeBeamCheck...],
  summary}`` — ``beams: []`` (not an error) when the model has no beam
  members; 400 on bad parameters / unknown combo names;
* ``POST /api/design/slab`` — ETABS-style column/middle strip flexural
  design of every meshed shell slab (body: ``{case?: name (default: the
  first DEAD-classified case), fc_prime?, bar_d?, cover?}``); response
  ``{preliminary, case, regions: [...]}`` — ``regions: []`` (not an
  error) when the model has no shell slabs; 400 on an unknown case / bad
  parameters;
* ``POST /api/results/vibration`` — AISC Design Guide 11 walking-
  vibration screen per slab-supporting beam (body: ``{case?: dead case,
  live_case?, live_factor?, beta?, ap_limit?}``); response
  ``{preliminary, case, live_case, beams: [VibrationCheck...]}``; 400 on
  unknown case names / bad parameters.

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
                                 live_load_reduction, make_rs_case_from_code,
                                 reduce_live_demands)
from skyframe.core.model import (BuildingModel, GridSystem,
                                 make_notional_pattern)
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
        """Apply the ASCE 7-16 combinations to the current model.

        Body: ``{standard?: "LRFD"|"ASD", SDS?: number}`` — ``SDS`` (v0.17,
        optional, >= 0) folds the vertical seismic component Ev = 0.2*SDS*D
        into the seismic combos per §12.4.2.3 (e.g. (1.2+0.2*SDS)D).
        """
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        standard = body.get("standard", "LRFD")
        try:
            sds = _num(body, "SDS", default=None)
            apply_asce7_combinations(_state["model"], standard=standard,
                                     SDS=sds)
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

    # ---------------------------------------- v0.11: Winkler foundation set
    @app.post("/api/member/foundation")
    def member_foundation():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        uids = body.get("member_uids")
        if not isinstance(uids, list) or not uids or not all(
                isinstance(u, str) and u for u in uids):
            return jsonify({"error": "'member_uids' must be a non-empty list "
                                     "of member uid strings"}), 400
        try:
            ks = _num(body, "ks", required=True)
            width = _num(body, "width", required=True)
            if ks < 0.0 or width < 0.0:
                raise ValueError("'ks' and 'width' must be >= 0")
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        model = _state["model"]
        by_uid = {m.uid: m for m in model.members}
        missing = [u for u in uids if u not in by_uid]
        if missing:
            return jsonify({"error": f"unknown member(s): "
                                     f"{', '.join(missing)}"}), 400
        for u in uids:
            m = by_uid[u]
            m.foundation_ks = float(ks)
            m.foundation_width = float(width)
        try:
            model.validate()
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(model.to_dict())

    # ---------------------------------------- v0.13: section-cut convenience
    @app.post("/api/section-cut")
    def add_section_cut():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return jsonify({"error": "'name' is required"}), 400
        axis = body.get("axis")
        if axis not in ("x", "y", "z"):
            return jsonify({"error": "'axis' must be one of x|y|z"}), 400

        def _rng(key):
            r = body.get(key)
            if r is None:
                return None
            if (not isinstance(r, list) or len(r) != 2
                    or not all(isinstance(v, (int, float))
                               and not isinstance(v, bool) for v in r)):
                raise ValueError(f"{key!r} must be a [lo, hi] pair of numbers")
            return [float(r[0]), float(r[1])]

        try:
            coord = _num(body, "coord", required=True)
            _state["model"].add_section_cut(
                name.strip(), axis, coord,
                x_range=_rng("x_range"), y_range=_rng("y_range"),
                z_range=_rng("z_range"))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    # ------------------------------------------ v0.14: grid system convenience
    @app.post("/api/grid")
    def add_grid():
        """Add or replace a named grid system on the current model.

        Body: ``{name, kind?, origin?, rotation?, x_lines?, y_lines?,
        radii?, theta_deg?}``.  ``kind`` defaults to "orthogonal"; a "radial"
        grid needs ``radii``.  Returns the updated model dict; 400 on bad
        input.
        """
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            return jsonify({"error": "'name' is required"}), 400
        kind = body.get("kind", "orthogonal")
        if kind not in ("orthogonal", "radial"):
            return jsonify({"error": "'kind' must be orthogonal|radial"}), 400

        def _numlist(key):
            v = body.get(key)
            if v is None:
                return []
            if (not isinstance(v, list)
                    or not all(isinstance(x, (int, float))
                               and not isinstance(x, bool) for x in v)):
                raise ValueError(f"{key!r} must be a list of numbers")
            return [float(x) for x in v]

        try:
            origin = body.get("origin", [0.0, 0.0])
            if (not isinstance(origin, list) or len(origin) != 2
                    or not all(isinstance(v, (int, float))
                               and not isinstance(v, bool) for v in origin)):
                raise ValueError("'origin' must be [x, y] numbers")
            rotation = body.get("rotation", 0.0)
            if isinstance(rotation, bool) or not isinstance(
                    rotation, (int, float)):
                raise ValueError("'rotation' must be a number (degrees)")
            gs = GridSystem(
                x_lines=_numlist("x_lines"), y_lines=_numlist("y_lines"),
                name=name.strip(),
                origin=(float(origin[0]), float(origin[1])),
                rotation=float(rotation), kind=kind,
                radii=_numlist("radii"), theta_deg=_numlist("theta_deg"))
            _state["model"].set_grid_system(gs)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_state["model"].to_dict())

    # ------------------------------------- v0.16: ASCE 7 live-load reduction
    @app.get("/api/live-reduction")
    def live_reduction():
        """ASCE 7-16 §4.7 reduced-live-load multipliers per column.

        Pure model geometry post-processing (no analysis): returns
        ``{"factors": {uid: {KLL, At, R, n_stories}}}`` for the current
        model.  This is a DESIGN-STAGE reduction — analysis results are
        never modified; pass ``live_reduction: true`` to the design
        endpoints to apply it to the checked demands.
        """
        try:
            factors = live_load_reduction(_state["model"])
        except (ValueError, TypeError) as exc:  # pragma: no cover
            return jsonify({"error": str(exc)}), 400
        return jsonify({"factors": factors})

    def _maybe_reduce_live(results, body):
        """(results-or-adjusted-dict, factors-or-None) for design endpoints.

        With ``body["live_reduction"]`` truthy, the live-attributable share
        of every column's member demands (in every case + additive combo) is
        scaled by that column's §4.7 multiplier R before checking
        (``reduce_live_demands``); ``body["live_case"]`` names the live CASE
        (default "LIVE").  Raises ValueError when the live case is unknown.
        """
        if not body.get("live_reduction"):
            return results, None
        live_case = body.get("live_case", "LIVE")
        if not isinstance(live_case, str) or not live_case:
            raise ValueError("'live_case' must be a case name string")
        factors = live_load_reduction(_state["model"])
        adjusted = reduce_live_demands(results.to_dict(), _state["model"],
                                       live_case=live_case,
                                       reduction=factors)
        return adjusted, factors

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
            src, factors = _maybe_reduce_live(results, body)
            if combos:
                names = combos if isinstance(combos, list) else None
                checks = check_members_envelope(
                    _state["model"], src, combos=names, **kw)
            else:
                checks = check_members(_state["model"], src, case, **kw)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        payload = {"preliminary": True, "case": case, "combos": combos,
                   "checks": [c.to_dict() for c in checks],
                   "summary": summarize(checks)}
        if factors is not None:
            payload["live_reduction"] = factors
        return jsonify(payload)

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
            src, factors = _maybe_reduce_live(results, body)
            if combos:
                names = combos if isinstance(combos, list) else None
                checks = check_concrete_members_envelope(
                    _state["model"], src, rebar, combos=names, **kw)
            else:
                checks = check_concrete_members(
                    _state["model"], src, case, rebar, **kw)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        payload = {"preliminary": True, "case": case, "combos": combos,
                   "checks": [c.to_dict() for c in checks],
                   "summary": summ_c(checks)}
        if factors is not None:
            payload["live_reduction"] = factors
        return jsonify(payload)

    # -------------------------------- v0.18: wall / punching / virtual work
    @app.post("/api/design/wall")
    def design_wall():
        """ACI 318 shear wall pier checks (runs analysis).

        Body: ``{combos?: [names], rho_v?, rho_h?, fy?, fc_prime?}`` —
        combos default to every additive combo; reinforcement/material
        parameters default to rho 0.0025 / fy 420 MPa / fc' from the wall
        concrete's E (ACI 19.2.2.1).  400 on bad parameters or when the
        model carries no pier forces.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.wall import check_wall_piers, summarize_walls
        body = request.get_json(silent=True) or {}
        combos = body.get("combos")
        if combos is not None and (
                not isinstance(combos, list)
                or not all(isinstance(c, str) and c for c in combos)):
            return jsonify({"error": "'combos' must be a list of combo/"
                                     "case name strings"}), 400
        try:
            kw = {}
            for key in ("rho_v", "rho_h", "fy", "fc_prime"):
                v = _num(body, key, default=None)
                if v is not None:
                    kw[key] = v
            results = OpenSeesEngine(_state["model"]).run()
            checks = check_wall_piers(_state["model"], results,
                                      combos=combos, **kw)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"preliminary": True, "combos": combos,
                        "piers": [c.to_dict() for c in checks],
                        "summary": summarize_walls(checks)})

    @app.post("/api/design/punching")
    def design_punching():
        """ACI two-way (punching) shear checks at slab columns (runs
        analysis when the model has meshed shell slabs).

        Body: ``{case?: name, fc_prime?, cover? (m)}`` — the case defaults
        to the first DEAD-classified case.  400 on an unknown case; an
        empty ``columns`` list (not an error) when the model has no shell
        slabs.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.punching import (check_punching,
                                              default_gravity_case)
        body = request.get_json(silent=True) or {}
        case = body.get("case")
        if case is not None and (not isinstance(case, str) or not case):
            return jsonify({"error": "'case' must be a case/combo name "
                                     "string"}), 400
        model = _state["model"]
        has_slabs = any(r.kind == "slab" and r.behavior == "shell"
                        for r in model.shells)
        try:
            kw = {}
            for key in ("fc_prime", "cover"):
                v = _num(body, key, default=None)
                if v is not None:
                    kw[key] = v
            if not has_slabs:
                # no slabs: no punching joints by definition — but an
                # explicitly named unknown case is still a caller error
                if case is not None and case not in model.cases \
                        and case not in model.combos:
                    raise KeyError(f"case/combo {case!r} not found")
                return jsonify({"preliminary": True, "case": case,
                                "columns": []})
            results = OpenSeesEngine(model).run()
            checks = check_punching(model, results, case, **kw)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"preliminary": True,
                        "case": checks[0].case if checks else case,
                        "columns": [c.to_dict() for c in checks]})

    @app.post("/api/results/virtual-work")
    def results_virtual_work():
        """Per-member virtual-work (unit-load) drift contributions.

        Body: ``{case: name, direction: "X"|"Y"}``.  Response:
        ``{contributions: {uid: e}, total, roof_disp, case, direction}``.
        400 on an unknown case, a bad direction, or a nonlinear/P-Delta
        model (the unit-load theorem needs linearity).
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        body = request.get_json(silent=True) or {}
        case = body.get("case")
        if not isinstance(case, str) or not case:
            return jsonify({"error": "'case' (a static case name) is "
                                     "required"}), 400
        direction = body.get("direction", "X")
        if direction not in ("X", "Y"):
            return jsonify({"error": "'direction' must be 'X' or 'Y'"}), 400
        try:
            out = OpenSeesEngine(_state["model"]).run_virtual_work(
                case, direction)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(out)

    # ------------------------------------------ v0.19: performance point
    @app.post("/api/results/performance-point")
    def results_performance_point():
        """ASCE 41-17 coefficient-method target displacement (docstring).

        Body: ``{case: pushover case name, SDS: g, SD1: g,
        site_class?: "A".."F" (default "D"), W?: kN (default: seismic
        weight from story + nodal masses)}``.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        body = request.get_json(silent=True) or {}
        model = _state["model"]
        case = body.get("case")
        if case not in model.pushover_cases:
            return jsonify({"error": f"Unknown pushover case {case!r}"}), 400
        try:
            sds = float(body["SDS"])
            sd1 = float(body["SD1"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "'SDS' and 'SD1' (design spectral "
                                     "parameters, g) are required"}), 400
        site = str(body.get("site_class", "D"))
        try:
            engine = OpenSeesEngine(model)
            po = engine.run_pushover(case)
            if len(po.roof_disp) < 2:
                return jsonify({"error": "pushover produced fewer than 2 "
                                         "converged steps"}), 400
            direction = model.pushover_cases[case].direction
            key = "ux" if direction == "X" else "uy"
            modal = engine.run_modal()
            mode = max(modal.participation, key=lambda p: p.get(key, 0.0))
            Ti = mode["T"]
            W = body.get("W")
            if W is None:
                masses = model.compute_story_masses()
                mass_t = (sum(masses.values())
                          + sum(nm.mx for nm in model.nodal_masses))
                W = mass_t * 9.81
            W = float(W)
            from skyframe.design.performance import target_displacement
            out = target_displacement(
                po.roof_disp, po.base_shear, Ti=Ti, W=W, SDS=sds, SD1=sd1,
                site_class=site,
                num_stories=len(model.stories))
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        # nearest converged step to delta_t + its hinge state counts
        step = min(range(len(po.roof_disp)),
                   key=lambda i: abs(po.roof_disp[i] - out["delta_t"]))
        summary = {"elastic": 0, "IO": 0, "LS": 0, "CP": 0, "collapse": 0}
        for hd in po.hinge_detail:
            if step < len(hd["state"]):
                summary[hd["state"][step]] += 1
        out.update({"case": case, "direction": direction, "step": step,
                    "roof_disp_at_step": po.roof_disp[step],
                    "hinge_summary": summary})
        return jsonify(out)

    # ------------------------------------------ v0.19: pattern live loading
    @app.post("/api/loads/pattern-live")
    def loads_pattern_live():
        """Skip live loading: derived patterns + PATTERN-LL envelope."""
        body = request.get_json(silent=True) or {}
        model = _state["model"]
        live = body.get("live_pattern")
        if live is None:
            live = next((p.name for p in model.patterns.values()
                         if p.kind == "live"), None)
            if live is None:
                return jsonify({"error": "the model has no live "
                                         "pattern"}), 400
        try:
            from skyframe.core.patterning import pattern_live_envelope
            pattern_live_envelope(model, live)
            model.validate()
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(model.to_dict())

    # ------------------------------------------ v0.19: auto sequence case
    @app.post("/api/case/auto-sequence")
    def case_auto_sequence():
        """One-click per-story staged-construction (sequential) case."""
        body = request.get_json(silent=True) or {}
        model = _state["model"]
        pattern = body.get("pattern")
        if pattern is None:
            pattern = next((p.name for p in model.patterns.values()
                            if p.kind == "dead"), None)
            if pattern is None:
                return jsonify({"error": "the model has no dead "
                                         "pattern"}), 400
        name = str(body.get("name") or "SEQ")
        live = body.get("include_live") or {}
        if not isinstance(live, dict):
            return jsonify({"error": "'include_live' must be a "
                                     "{pattern: factor} object"}), 400
        try:
            model.add_staged_case(
                name, pattern=pattern,
                include_live={str(p): float(f) for p, f in live.items()})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(model.to_dict())

    # -------------------------------- v0.20: composite / slab / vibration
    @app.post("/api/design/composite")
    def design_composite():
        """AISC 360-16 I3 composite beam checks (runs analysis).

        Body: ``{combos?: [names], fc_prime?, t_slab?, hr?, stud_d?,
        stud_Fu?, rib_spacing?, shored?: bool}`` — combos default to
        every additive combo; slab thickness / fc' default from the slab
        the beam supports.  ``beams: []`` (not an error) when the model
        has no beam members.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.composite import (check_composite_beams,
                                               summarize_composite)
        body = request.get_json(silent=True) or {}
        combos = body.get("combos")
        if combos is not None and (
                not isinstance(combos, list)
                or not all(isinstance(c, str) and c for c in combos)):
            return jsonify({"error": "'combos' must be a list of combo/"
                                     "case name strings"}), 400
        shored = body.get("shored", False)
        if not isinstance(shored, bool):
            return jsonify({"error": "'shored' must be a boolean"}), 400
        model = _state["model"]
        try:
            kw = {}
            for key in ("fc_prime", "t_slab", "hr", "stud_d", "stud_Fu",
                        "rib_spacing"):
                v = _num(body, key, default=None)
                if v is not None:
                    kw[key] = v
            params = {"shored": shored, **kw}
            if not any(m.kind == "beam" for m in model.members):
                # no beams: nothing to check by definition — but an
                # explicitly named unknown combo is still a caller error
                for c in combos or []:
                    if c not in model.cases and c not in model.combos:
                        raise KeyError(f"case/combo {c!r} not found")
                return jsonify({"preliminary": True, "combos": combos,
                                "params": params, "beams": [],
                                "summary": summarize_composite([])})
            results = OpenSeesEngine(model).run()
            checks = check_composite_beams(model, results, combos,
                                           shored=shored, **kw)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"preliminary": True, "combos": combos,
                        "params": params,
                        "beams": [c.to_dict() for c in checks],
                        "summary": summarize_composite(checks)})

    @app.post("/api/design/slab")
    def design_slab():
        """ETABS-style slab strip flexural design (runs analysis when the
        model has meshed shell slabs).

        Body: ``{case?: name, fc_prime?, bar_d?, cover? (m)}`` — the case
        defaults to the first DEAD-classified case.  400 on an unknown
        case; ``regions: []`` (not an error) when the model has no shell
        slabs.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.slab import check_slab_strips
        body = request.get_json(silent=True) or {}
        case = body.get("case")
        if case is not None and (not isinstance(case, str) or not case):
            return jsonify({"error": "'case' must be a case/combo name "
                                     "string"}), 400
        model = _state["model"]
        has_slabs = any(r.kind == "slab" and r.behavior == "shell"
                        for r in model.shells)
        try:
            kw = {}
            for key in ("fc_prime", "bar_d", "cover"):
                v = _num(body, key, default=None)
                if v is not None:
                    kw[key] = v
            if not has_slabs:
                # no slabs: no strips by definition — but an explicitly
                # named unknown case is still a caller error
                if case is not None and case not in model.cases \
                        and case not in model.combos:
                    raise KeyError(f"case/combo {case!r} not found")
                return jsonify({"preliminary": True, "case": case,
                                "regions": []})
            results = OpenSeesEngine(model).run()
            regions = check_slab_strips(model, results, case, **kw)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"preliminary": True,
                        "case": regions[0]["case"] if regions else case,
                        "regions": regions})

    @app.post("/api/results/vibration")
    def results_vibration():
        """DG11 walking-vibration screen per slab-supporting beam (runs
        analysis).

        Body: ``{case?: dead case, live_case?, live_factor?, beta?,
        ap_limit?}`` — the case defaults to the first DEAD-classified
        case, the live case to the first LIVE-classified one.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.vibration import check_vibration
        body = request.get_json(silent=True) or {}
        model = _state["model"]
        names = {}
        for key in ("case", "live_case"):
            v = body.get(key)
            if v is not None and (not isinstance(v, str) or not v):
                return jsonify({"error": f"{key!r} must be a case/combo "
                                         "name string"}), 400
            if v is not None and v not in model.cases \
                    and v not in model.combos:
                return jsonify({"error": f"case/combo {v!r} not found"}), 400
            names[key] = v
        try:
            kw = {}
            for key in ("live_factor", "beta", "ap_limit"):
                v = _num(body, key, default=None)
                if v is not None:
                    kw[key] = v
            results = OpenSeesEngine(model).run()
            checks = check_vibration(model, results, names["case"],
                                     names["live_case"], **kw)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        case = names["case"]
        live = names["live_case"]
        return jsonify({"preliminary": True, "case": case,
                        "live_case": live,
                        "beams": [c.to_dict() for c in checks]})

    # --------------------------------------------- v0.12: steel optimization
    @app.post("/api/design/optimize")
    def design_optimize():
        """Single-pass demand-based steel section optimization (runs analysis).

        Body: {"case": "<name>", optional "target_ratio" (default 0.95),
        "candidates" (list of library W-shape names), "apply" (bool),
        "Fy","kx","ky","Lb"}.  With ``apply`` true the suggestions are
        assigned onto the current model and the updated model dict is echoed;
        otherwise only the suggestion list is returned.
        """
        if not _OPENSEES_OK:
            return jsonify({"error": "OpenSeesPy is not available"}), 400
        from skyframe.design.steel import (apply_suggestions,
                                           optimize_members)
        body = request.get_json(silent=True) or {}
        case = body.get("case")
        if not isinstance(case, str) or not case:
            return jsonify({"error": "'case' (name of a case/combo) is "
                                     "required"}), 400
        candidates = body.get("candidates")
        if candidates is not None and (
                not isinstance(candidates, list)
                or not all(isinstance(c, str) and c for c in candidates)):
            return jsonify({"error": "'candidates' must be a list of "
                                     "library W-shape names"}), 400
        apply_flag = bool(body.get("apply", False))
        try:
            target = _num(body, "target_ratio", default=0.95)
            if not (target > 0.0):
                raise ValueError("'target_ratio' must be > 0")
            kw = {k: float(body[k]) for k in ("Fy", "kx", "ky", "Lb")
                  if isinstance(body.get(k), (int, float))
                  and not isinstance(body.get(k), bool)}
            model = _state["model"]
            results = OpenSeesEngine(model).run()
            suggestions = optimize_members(
                model, results, case, candidates,
                target_ratio=float(target), **kw)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        payload: Dict[str, Any] = {
            "case": case, "applied": apply_flag,
            "suggestions": [s.to_dict() for s in suggestions]}
        if apply_flag:
            apply_suggestions(model, suggestions)
            payload["model"] = model.to_dict()
        return jsonify(payload)

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
