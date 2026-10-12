"""HTTP endpoints of the code spectra, code design combinations, pattern
types and live-load reduction (skyframe.core.spectra_codes / combos_codes /
llr_codes); registered by ``create_app`` with one line.  CONTRACT "Code
spectra, design combinations, pattern types and live-load reduction".
"""

from __future__ import annotations

import warnings
from typing import Any, Dict

from flask import jsonify, request


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import combos_codes as _cc
    from skyframe.core import llr_codes as _llr
    from skyframe.core import spectra_codes as _sc

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _err(exc):
        return jsonify({"error": str(exc)}), 400

    def _model(b):
        """``body.model`` (a draft, never stored) or the current model."""
        if b.get("model") is not None:
            from skyframe.core.model import BuildingModel
            return BuildingModel.from_dict(b["model"])
        return state["model"]

    # ------------------------------------------------------- spectra
    @app.get("/api/codes/spectra")
    def lc_spectra_catalogue():
        return jsonify({"codes": _sc.catalogue(),
                        "mprs_periods": list(_sc.MPRS_PERIODS)})

    @app.post("/api/function/spectrum-code/preview")
    def lc_spectrum_preview():
        b = _body()
        try:
            return jsonify(_sc.spectrum(b.get("code"), b.get("params")))
        except (ValueError, TypeError, KeyError) as exc:
            return _err(exc)

    @app.post("/api/function/spectrum-code")
    def lc_spectrum_create():
        """Body ``{name, code, params?, damping?}``: add / replace the
        named spectrum function; returns the model."""
        b = _body()
        try:
            dmp = b.get("damping")
            _sc.make_spectrum_function(state["model"], b.get("name"),
                                       b.get("code"), b.get("params"),
                                       damping=dmp)
        except (ValueError, TypeError, KeyError) as exc:
            return _err(exc)
        return jsonify(state["model"].to_dict())

    # ------------------------------------------------------- combos
    @app.get("/api/codes/combos")
    def lc_combos_catalogue():
        return jsonify({"standards": _cc.catalogue(),
                        "pattern_kinds": list(_cc.PATTERN_KINDS),
                        "kind_labels": dict(_cc.PATTERN_KIND_LABELS),
                        "kind_groups": dict(_cc.KIND_GROUP)})

    def _gen(b, apply_):
        model = state["model"] if apply_ else _model(b)
        std = b.get("standard")
        params = b.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        inc = b.get("include_rs", True)
        if not isinstance(inc, bool):
            raise ValueError("include_rs must be true/false")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            if apply_:
                rep = b.get("replace", True)
                if not isinstance(rep, bool):
                    raise ValueError("replace must be true/false")
                out = _cc.apply_code_combinations(
                    model, std, replace=rep, include_rs=inc,
                    **params)
            else:
                out = _cc.code_combinations(model, std,
                                            include_rs=inc, **params)
        return out, [str(w.message) for w in caught]

    @app.post("/api/combos/code/preview")
    def lc_combos_preview():
        """Body ``{standard, params?, include_rs?, model?}`` -> ``{combos:
        [{name, factors, desc}], warnings}``; never changes the model."""
        try:
            out, w = _gen(_body(), False)
        except (ValueError, TypeError, KeyError) as exc:
            return _err(exc)
        return jsonify({"combos": out, "warnings": w})

    @app.post("/api/combos/code")
    def lc_combos_apply():
        """Body ``{standard, params?, include_rs?, replace?}``; returns the
        model (like POST /api/combos/asce7)."""
        try:
            _gen(_body(), True)
        except (ValueError, TypeError) as exc:
            return _err(exc)
        return jsonify(state["model"].to_dict())

    # ------------------------------------------------------- LLR
    @app.post("/api/live-reduction/factors")
    def lc_llr_factors():
        """Body ``{settings?, model?}`` (default: the model's
        llr_settings) -> element factors + reducible live cases."""
        b = _body()
        try:
            model = _model(b)
            st = b.get("settings", model.llr_settings)
            f = _llr.element_factors(model, st)
            live, w = _llr.reducible_cases(model, st)
        except (ValueError, TypeError, KeyError) as exc:
            return _err(exc)
        f["live_cases"] = live
        f["warnings"] += w
        return jsonify(f)

    @app.post("/api/results/live-reduced")
    def lc_llr_results():
        """Body ``{settings?}``: reduced column / pier forces of the last
        analysis of the current model (runs it when none is stored)."""
        b = _body()
        model = state["model"]
        st = b.get("settings", model.llr_settings)
        try:
            _llr.normalize_settings(st)
        except (ValueError, TypeError) as exc:
            return _err(exc)
        from skyframe.api import server as _srv
        res, _ctx = _srv._tables_lookup(model)
        if res is None:
            try:
                from skyframe.engine.opensees_engine import OpenSeesEngine
                res = OpenSeesEngine(model).run().to_dict()
            except Exception as exc:                 # pragma: no cover
                return _err(exc)
        try:
            return jsonify(_llr.apply_to_results(res, model, st))
        except (ValueError, TypeError) as exc:
            return _err(exc)
