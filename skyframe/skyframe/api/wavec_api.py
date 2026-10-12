"""Wave C HTTP endpoints (registered by ``create_app`` with one line).
CONTRACT "Diaphragm design forces (ASCE 7 12.10)", "Live-load reduction
for beams", "Static seismic eccentricity overrides".

* ``POST /api/fpx`` body ``{pattern?, SDS?, Ie?, method?, weights?, wpx?,
  Rs?, Cs?, Omega0?, zs?, SD1?, model?}`` -> ``{"reports": [...]}``
  (:func:`skyframe.core.fpx.compute` for ``pattern``, else every pattern of
  the settings: the body's keys over ``model.fpx_settings``).  Read-only.
* ``POST /api/pattern/fpx`` body ``{pattern, SDS, Ie?, method?, ...,
  prefix?, copy_ecc?}`` -> writes one Fpx pattern per level into the
  CURRENT model and returns ``{"model", "patterns", "report"}``.
* ``POST /api/llr/beams`` body ``{settings?, model?}`` -> ``{"beams":
  {uid: {At, A1, n, R, L, w_left, w_right}}}`` (beam LLR preview).
* ``POST /api/pattern/ecc-torques`` body ``{pattern, model?}`` -> ``{"rows":
  [{story, fx, fy, mz_x, mz_y, override}]}`` (hand-check of the static
  eccentricity torques on the master-node path).

``model`` (optional) is a full model dict to evaluate instead of the
server's current model (the SPA sends its working copy).
"""
from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

from skyframe.core import fpx as _fpx
from skyframe.core import llr_codes as _llr
from skyframe.core import static_ecc as _secc

_FPX_KW = ("weights", "wpx", "method", "Rs", "Cs", "Omega0", "zs", "SD1")


def register(app, state: Dict[str, Any]) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _model(body):
        md = body.get("model")
        if isinstance(md, dict):
            from skyframe.core.model import BuildingModel
            return BuildingModel.from_dict(md)
        return state["model"]

    @app.post("/api/fpx")
    def fpx_compute():
        body = _body()
        try:
            model = _model(body)
            if body.get("pattern"):
                kw = {k: body[k] for k in _FPX_KW if body.get(k) is not None}
                rep = _fpx.compute(model, str(body["pattern"]),
                                   float(body.get("SDS", 0.0)),
                                   float(body.get("Ie", 1.0)),
                                   direction=body.get("direction"), **kw)
                return jsonify({"reports": [rep]})
            s = dict(getattr(model, "fpx_settings", None) or {})
            for k in ("SDS", "Ie", "patterns") + _FPX_KW:
                if body.get(k) is not None:
                    s[k] = body[k]
            return jsonify({"reports": _fpx.compute_settings(model, s)})
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/pattern/fpx")
    def fpx_generate():
        body = _body()
        pat = body.get("pattern")
        if not isinstance(pat, str) or not pat:
            return jsonify({"error": "'pattern' is required"}), 400
        try:
            model = state["model"]
            kw = {k: body[k] for k in _FPX_KW if body.get(k) is not None}
            rep = _fpx.compute(model, pat, float(body.get("SDS", 0.0)),
                               float(body.get("Ie", 1.0)), **kw)
            names = _fpx.generate_patterns(
                model, pat, float(body["SDS"]), float(body.get("Ie", 1.0)),
                prefix=body.get("prefix"),
                copy_ecc=bool(body.get("copy_ecc", True)), **kw)
            model.validate()
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"model": model.to_dict(), "patterns": names,
                        "report": rep})

    @app.post("/api/llr/beams")
    def llr_beams():
        body = _body()
        try:
            model = _model(body)
            st = body.get("settings")
            if st is None:
                st = getattr(model, "llr_settings", None) or {}
            st = dict(st)
            st["apply_to"] = sorted(set(st.get("apply_to",
                                               ["columns", "piers"]))
                                    | {"beams"})
            f = _llr.element_factors(model, st)
            return jsonify({"code": f["code"], "beams": f["beams"]})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/pattern/ecc-torques")
    def ecc_torques():
        body = _body()
        try:
            model = _model(body)
            name = body.get("pattern")
            if name not in model.patterns:
                return jsonify({"error": f"unknown pattern {name!r}"}), 400
            pat = model.patterns[name]
            _secc.validate(model, pat)
            return jsonify({"rows": _secc.story_torque_table(model, pat)})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
