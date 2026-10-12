"""Wave C HTTP endpoints (CONTRACT "Wave C: regional seismic / wind
generators, code spectra, spectrum file import, USGS lookup and regional
material / steel libraries"); registered by ``create_app`` with one line.

* ``GET  /api/codes/ec8-annexes``             EC8 national-annex presets
* ``POST /api/function/spectrum-file/preview`` parse a spectrum file
* ``POST /api/function/spectrum-file``         add a spectrum function
* ``POST /api/usgs/designmaps``                optional USGS site lookup

The new auto-lateral generators and code spectra need no endpoint: they
register into the existing ``POST /api/pattern/auto-lateral`` and
``/api/function/spectrum-code`` machinery.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import seismic_codes_wavec as _sxc
    from skyframe.core import spectra_codes_wavec as _scw
    from skyframe.core import usgs_lookup as _usgs

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _err(exc, code=400):
        return jsonify({"error": str(exc)}), code

    def _file_args(b):
        return {"period_units": b.get("period_units", "s"),
                "value_units": b.get("value_units", "g"),
                "skip_lines": b.get("skip_lines", 0),
                "scale": b.get("scale", 1.0)}

    @app.get("/api/codes/ec8-annexes")
    def wvc_ec8_annexes():
        """EN 1998-1 national-annex parameter presets (values entered from
        memory: ``from_memory`` true, see docs/REVIEW_FLAGS.md)."""
        return jsonify({"annexes": _sxc.ec8_na_catalogue()})

    @app.post("/api/function/spectrum-file/preview")
    def wvc_spectrum_file_preview():
        """Body ``{text, period_units?, value_units?, skip_lines?, scale?}``
        -> ``{points, n, damping, header_lines, ...}``; no model change."""
        b = _body()
        try:
            return jsonify(_scw.parse_spectrum_text(b.get("text"),
                                                    **_file_args(b)))
        except (ValueError, TypeError) as exc:
            return _err(exc)

    @app.post("/api/function/spectrum-file")
    def wvc_spectrum_file_create():
        """Body ``{name, text, period_units?, value_units?, skip_lines?,
        scale?, damping?}``: add / replace the spectrum function; returns
        the model."""
        b = _body()
        try:
            _scw.import_spectrum_function(state["model"], b.get("name"),
                                          b.get("text"),
                                          damping=b.get("damping"),
                                          **_file_args(b))
        except (ValueError, TypeError) as exc:
            return _err(exc)
        return jsonify(state["model"].to_dict())

    @app.post("/api/usgs/designmaps")
    def wvc_usgs_designmaps():
        """Body ``{latitude, longitude, edition?, site_class?,
        risk_category?}`` -> ``{ok: true, values, spectra, mprs_pairs?}``;
        an unreachable service answers ``{ok: false, error, manual: true}``
        (HTTP 200: the lookup is optional, the values can be typed by hand)."""
        b = _body()
        try:
            out = _usgs.lookup(b.get("latitude"), b.get("longitude"),
                               edition=b.get("edition", "asce7-22"),
                               site_class=b.get("site_class", "D"),
                               risk_category=b.get("risk_category", "II"))
        except ValueError as exc:
            return _err(exc)
        except _usgs.UsgsUnavailable as exc:
            return jsonify({"ok": False, "error": str(exc),
                            "manual": True})
        out["ok"] = True
        return jsonify(out)
