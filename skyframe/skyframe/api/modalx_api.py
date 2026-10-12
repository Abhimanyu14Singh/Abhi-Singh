"""HTTP endpoints: time-history function tools, named modal cases and the
full-assembly Ritz analysis (CONTRACT "Time-history function input" and
"Multiple mass sources, modal cases, Ritz basis").  Registered by
``create_app`` with one line; no endpoint mutates the current model.

* ``POST /api/th_function/import``   ``{text, format: "at2"|"time_value"|
  "equal", dt?, header_lines?, units?, scale?}`` -> ``{values, dt,
  source, warnings}`` (values m/s^2)
* ``POST /api/th_function/generate`` ``{kind, ...params}`` ->
  ``{values, dt, source}``
* ``POST /api/th_function/match``    ``{values | function, dt?, target |
  spectrum_function, damping?, t_min?, t_max?, max_iter?, tol?,
  n_periods?, target_units?}`` -> :func:`skyframe.core.thfunc.
  match_spectrum` dict
* ``POST /api/analyze/modal_case``   ``{case, model?}`` -> the modal
  results of one named modal case (``ModalResults.to_dict()`` + ``case``,
  ``type``, ``mass_source``, ``warnings``)
* ``POST /api/analyze/ritz_full``    ``{n?, starts?, max_cycles?,
  mass_source?, model?}`` -> Ritz vectors on the ENGINE assembly (shells,
  links, diaphragms included; shapes keyed by engine node tags)
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request

from skyframe.core.model import BuildingModel

_ERRORS = (ValueError, KeyError, TypeError, RuntimeError, ArithmeticError)


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def full_ritz(model, body: dict) -> bool:
    """``/api/analyze/ritz`` routing: the engine assembly whenever the
    frame-only numpy path would skip something (shells / links) or the
    body asks for engine starts; frame-only legacy requests are
    unchanged (byte-identical)."""
    return bool(model.shells or model.links or body.get("starts")
                or body.get("mass_source"))


def ritz_full(model, body: dict) -> dict:
    """Engine-assembly Ritz in the RitzResults dict shape (+ assembly)."""
    from skyframe.core import modal_cases as _mcs
    from skyframe.engine import modal_cases as _mcx
    from skyframe.engine.opensees_engine import OpenSeesEngine
    import copy
    n = body.get("n")
    if n is not None and (isinstance(n, bool) or not isinstance(n, int)
                          or n < 1):
        raise ValueError("'n' must be a positive integer")
    direction = body.get("direction", "X")
    starts = body.get("starts")
    if starts is None:
        if direction not in ("X", "Y", "XY"):
            raise ValueError("direction must be one of ('X', 'Y', 'XY')")
        starts = [{"type": "accel", "dir": "U" + d} for d in direction]
    m = copy.deepcopy(model)
    spec = _mcs.normalize_modal_case({
        "type": "ritz", "num_modes": int(n or model.num_modes),
        "ritz_starts": starts, "max_cycles": body.get("max_cycles", 0),
        "mass_source": body.get("mass_source") or ""})
    m.modal_cases = dict(m.modal_cases or {})
    m.modal_cases["__RITZ__"] = spec
    _mcs.validate(m)
    sub_m, spec = _mcx.prepare_model(m, "__RITZ__")
    eng = OpenSeesEngine(sub_m)
    _mcx.attach_spec(eng, spec)
    res = eng.run_modal()
    d = res.to_dict()
    d.pop("load_participation", None)
    d["direction"] = direction if body.get("starts") is None else "starts"
    d["warnings"] = list(eng.__dict__.get("_ritz_warnings", []))
    d["assembly"] = "engine"
    return d


def ritz_route(model, body: dict):
    """Flask response of :func:`ritz_full` (400 on bad input)."""
    try:
        return jsonify(ritz_full(model, body))
    except _ERRORS as exc:
        return jsonify({"error": str(exc)}), 400


def register(app, state: Dict[str, Any]) -> None:

    def _model(body):
        return (BuildingModel.from_dict(body["model"])
                if body.get("model") is not None else state["model"])

    @app.post("/api/th_function/import")
    def th_function_import():
        from skyframe.core import thfunc as _thf
        b = _body()
        try:
            res = _thf.import_function(
                b.get("text", ""), b.get("format", "at2"), dt=b.get("dt"),
                header_lines=b.get("header_lines", 0), units=b.get("units"),
                scale=b.get("scale", 1.0))
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(res)

    @app.post("/api/th_function/generate")
    def th_function_generate():
        from skyframe.core import thfunc as _thf
        b = dict(_body())
        kind = b.pop("kind", None)
        try:
            res = _thf.generate_function(kind, **b)
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(res)

    @app.post("/api/th_function/match")
    def th_function_match():
        from skyframe.core import thfunc as _thf
        b = _body()
        model = state["model"]
        try:
            vals, dt = b.get("values"), b.get("dt")
            if vals is None and b.get("function"):
                fn = model.th_functions[b["function"]]
                vals, dt = fn.values, (dt or fn.dt)
            target = b.get("target")
            if target is None and b.get("spectrum_function"):
                target = model.spectrum_functions[
                    b["spectrum_function"]].points
            if vals is None or target is None:
                raise ValueError("give the seed record (values + dt, or "
                                 "function) and the target (target or "
                                 "spectrum_function)")
            kw = {k: b[k] for k in ("damping", "t_min", "t_max",
                                    "max_iter", "tol", "n_periods",
                                    "target_units") if k in b}
            res = _thf.match_spectrum(vals, dt, target, **kw)
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(res)

    @app.post("/api/analyze/modal_case")
    def analyze_modal_case():
        from skyframe.core import modal_cases as _mcs
        from skyframe.engine import modal_cases as _mcx
        from skyframe.engine.opensees_engine import OpenSeesEngine
        b = _body()
        name = b.get("case")
        if not isinstance(name, str) or not name:
            return jsonify({"error": "'case' (a modal case name) is "
                                     "required"}), 400
        try:
            model = _model(b)
            if name not in (model.modal_cases or {}):
                raise ValueError(f"Unknown modal case {name!r}")
            eng = OpenSeesEngine(model)
            res = _mcx.run_modal_case(eng, name)
            spec = _mcs.normalize_modal_case(model.modal_cases[name])
            d = res.to_dict()
            d.update({"case": name, "type": spec["type"],
                      "mass_source": spec["mass_source"],
                      "warnings": list(_mcx.sub_engine(eng, name).__dict__
                                       .get("_ritz_warnings", []))})
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(d)

    @app.post("/api/analyze/ritz_full")
    def analyze_ritz_full():
        b = _body()
        try:
            return jsonify(ritz_full(_model(b), b))
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
