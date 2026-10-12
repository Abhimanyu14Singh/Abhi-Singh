"""HTTP endpoints for time-dependent materials and tendon losses (CONTRACT
"Time-dependent materials, incremental creep and tendon elements").
Registered by ``create_app`` with one line; no endpoint mutates the
current model.

* ``POST /api/timedep/curves`` ``{props, material?, E?, fc?, t0?, times?,
  model?}`` -> :func:`skyframe.core.timedep.curves` (``material`` takes
  ``E`` / ``fc`` from that model material; explicit ``E`` / ``fc`` win;
  default E = 30e6 kPa)
* ``POST /api/timedep/tendon_losses`` ``{tendon | uid, model?}`` ->
  ``{"P0", "length", "s", "P", "anchor_set_length", "elements"?}`` — the
  force profile after friction / wobble / anchor set / ES factor /
  long-term (and, for ``model_as: "elements"``, the element chords)
* ``POST /api/timedep/relaxation`` ``{relaxation, sigma_pi, fpk?, fpy?,
  times?}`` -> ``{"times", "ratio"}``
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

from skyframe.core import timedep as _tdm
from skyframe.core.model import BuildingModel

_ERRORS = (ValueError, KeyError, TypeError, ArithmeticError)


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def _model(state, body):
    if body.get("model") is not None:
        return BuildingModel.from_dict(body["model"])
    return state["model"]


def register(app, state: Dict[str, Any]) -> None:

    def _err(exc, code=400):
        msg = (exc.args[0] if isinstance(exc, KeyError) and exc.args
               else str(exc))
        return jsonify({"error": str(msg)}), code

    @app.post("/api/timedep/curves")
    def timedep_curves():
        body = _body()
        try:
            E, fc = 30e6, None
            mname = body.get("material")
            if mname:
                model = _model(state, body)
                mat = model.materials[mname]
                E, fc = mat.E, mat.fc
            if body.get("E") is not None:
                E = float(body["E"])
            if body.get("fc") is not None:
                fc = float(body["fc"])
            props = _tdm.normalize_props(body.get("props") or {})
            return jsonify(_tdm.curves(props, E, fc,
                                       float(body.get("t0", 28.0)),
                                       body.get("times")))
        except _ERRORS as exc:
            return _err(exc)

    @app.post("/api/timedep/tendon_losses")
    def timedep_tendon_losses():
        body = _body()
        try:
            from skyframe.core import tendons as _td
            from skyframe.core import tendon_elements as _cte
            model = _model(state, body)
            raw = body.get("tendon")
            if raw is None:
                uid = body.get("uid")
                raw = next(t for t in model.tendons if t.get("uid") == uid)
            td = _td.normalize_tendon(raw)
            pieces = _td.sub_pieces(model, td)
            prof = _td.force_profile(model, td, pieces)
            out = {"P0": prof["P0"], "length": prof["length"],
                   "s": prof["s_bound"], "P": prof["P_bound"],
                   "anchor_set_length": prof["anchor_set_length"],
                   "model_as": td.get("model_as", "loads")}
            if _cte.is_element(td):
                out["elements"] = [[list(a), list(b)] for a, b in
                                   _cte.chord_points(td, pieces)]
            return jsonify(out)
        except StopIteration:
            return _err(KeyError("unknown tendon uid"), 404)
        except _ERRORS as exc:
            return _err(exc)

    @app.post("/api/timedep/relaxation")
    def timedep_relaxation():
        body = _body()
        try:
            r = _tdm.normalize_relaxation(body.get("relaxation") or {})
            sig = float(body["sigma_pi"])
            times = body.get("times") or [1, 10, 100, 1000, 10000, 100000]
            fpk = body.get("fpk")
            fpy = body.get("fpy")
            return jsonify({"times": times, "ratio": [
                _tdm.relaxation_ratio(r, sig, float(t), fpk, fpy)
                for t in times]})
        except _ERRORS as exc:
            return _err(exc)
