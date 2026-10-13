"""HTTP endpoint of the Wave G results toolbar (step / envelope states of
time-history, pushover, nonlinear static and staged cases for the
graphics); registered by ``create_app`` with one line.  CONTRACT "Step
and envelope results for graphics".

``POST /api/results/steps`` body::

    {"case": "<TH | pushover | nonlinear static | staged case>",
     "frames": 40,          # evenly spaced recorded steps (1..400)
     "members": true,       # member station forces at the steps:
                            #   true = in this response, false = not
                            #   recorded, "lazy" = recorded, served by step
     "envelope": false,     # + signed max / min over every step
     "member_steps": [..]}  # with members "lazy": the station blocks of
                            #   these recorded steps only

Re-runs that one case on a fresh engine of the CURRENT model with the
step recorder of :mod:`skyframe.engine.step_results` on (the model and
the stored results are never touched).  The recorded run is cached (keyed
by the model JSON and the request), so the "lazy" index response and the
later ``member_steps`` slices come from ONE analysis: a large model's
station blocks (tens of MB for 40 steps of 2 000 members) are fetched
only for the steps a view shows.  Floats are written with 12 significant
digits (relative 5e-13, inside the 1e-9 contract).  400 with
``{"error"}`` on a bad body / unknown case / unsupported type.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, Dict

from flask import Response, jsonify, request

_CACHE_MAX = 3


def _compact(o):
    if isinstance(o, float):
        return float(f"{o:.12g}")
    if isinstance(o, dict):
        return {k: _compact(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_compact(v) for v in o]
    return o


def _json(obj) -> Response:
    return Response(json.dumps(_compact(obj), separators=(",", ":")),
                    mimetype="application/json")


def register(app, state: Dict[str, Any]) -> None:
    cache: "OrderedDict[str, dict]" = OrderedDict()

    @app.post("/api/results/steps")
    def results_steps():
        """Step / envelope states of one multi-step case (graphics)."""
        from skyframe.engine import step_results as _gsr
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "body must be a JSON object"}), 400
        mode = body.get("members", True)
        if mode not in (True, False, "lazy"):
            return jsonify({"error": "'members' must be true, false or "
                                     "\"lazy\""}), 400
        steps_req = body.get("member_steps")
        if steps_req is not None and (
                not isinstance(steps_req, list) or not all(
                    isinstance(s, int) and not isinstance(s, bool)
                    for s in steps_req) or len(steps_req) > 400):
            return jsonify({"error": "'member_steps' must be a list of "
                                     "step numbers (at most 400)"}), 400
        try:
            req = _gsr.normalize_request(
                dict({k: v for k, v in body.items()
                      if k not in ("members", "member_steps")},
                     members=mode is not False))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        model = state["model"]
        try:
            sig = hashlib.sha1(json.dumps(model.to_dict(), sort_keys=True,
                                          default=str).encode()).hexdigest()
        except Exception:                      # pragma: no cover - defensive
            sig = str(id(model))
        key = sig + "|" + json.dumps(req, sort_keys=True)
        cached = key in cache
        if cached:
            cache.move_to_end(key)
            pay = cache[key]
        else:
            try:
                pay = _gsr.run_steps(model, req)
            except (ValueError, KeyError, RuntimeError) as exc:
                return jsonify({"error": str(exc)}), 400
            cache[key] = pay
            while len(cache) > _CACHE_MAX:
                cache.popitem(last=False)
        if steps_req is not None:
            ms = pay.get("member_stations") or {}
            return _json({"case": pay["case"], "kind": pay["kind"],
                          "member_stations": {str(s): ms[str(s)]
                                              for s in steps_req
                                              if str(s) in ms},
                          "cached": cached})
        out = dict(pay, cached=cached)
        if mode == "lazy":
            out.pop("member_stations", None)
            out["members_lazy"] = True
        return _json(out)
