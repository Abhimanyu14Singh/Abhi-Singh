"""HTTP endpoints of Wave C (C9): Section Designer moment-curvature and the
anisotropic / rotated shell stiffness preview.  Registered by
``create_app`` with one line.  CONTRACT "Anisotropic shells, material
angle, Section Designer moment-curvature, layered shells with any
material, results CSV folder".

* ``POST /api/sections/designer/mc`` body ``{name | section, P?: kN
  (compression +), angle?: deg, concrete?: "simple" | "mander" |
  "mander_confined" | "park" | "material", steel?: "simple" | "epp" |
  "park" | "material", params?: {eps_c0, eps_cu, fcc_ratio, eps_cu_conf,
  eps_su, eps_sh, b, ft}, n_strips?, n_steps?}`` -> the
  :func:`skyframe.core.moment_curvature.moment_curvature` dict + ``rows``
  (:func:`~skyframe.core.moment_curvature.table_rows`).  ``section`` (a
  designer-section dict) analyses an unsaved section.
* ``POST /api/shell_sections/stiffness`` body ``{name}`` or ``{section,
  materials?}`` -> ``{angle, A, D, S13, S23}`` (element axes, before
  modifiers; :func:`skyframe.core.aniso.summary`).
Errors: 400 ``{error}``.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from flask import jsonify, request


def _clean(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def register(app, state: Dict[str, Any]) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    @app.post("/api/sections/designer/mc")
    def designer_moment_curvature():
        from skyframe.core import moment_curvature as _mc
        from skyframe.core.sections_designer import DesignerSection
        body = _body()
        model = state["model"]
        try:
            if isinstance(body.get("section"), dict):
                ds = DesignerSection.from_dict(body["section"])
            else:
                ds = model.designer_sections.get(body.get("name"))
                if ds is None:
                    return jsonify({"error": "unknown designer section "
                                             f"{body.get('name')!r}"}), 400
            out = _mc.moment_curvature(
                ds, model.materials, P=body.get("P", 0.0),
                angle=body.get("angle", 0.0),
                concrete=str(body.get("concrete", "simple")),
                steel=str(body.get("steel", "simple")),
                params=body.get("params"),
                n_strips=body.get("n_strips", _mc.N_STRIPS),
                n_steps=body.get("n_steps", _mc.N_STEPS))
            out["rows"] = _mc.table_rows(out)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_clean(out))

    @app.post("/api/shell_sections/stiffness")
    def shell_stiffness():
        from skyframe.core import aniso as _an
        from skyframe.core.model import ShellSection, material_from_dict
        body = _body()
        model = state["model"]
        try:
            mats = dict(model.materials)
            for n, md in (body.get("materials") or {}).items():
                mats[n] = material_from_dict(md, n)
            if isinstance(body.get("section"), dict):
                sd = body["section"]
                ss = ShellSection(name=str(sd.get("name", "S")),
                                  material=str(sd["material"]),
                                  thickness=float(sd["thickness"]),
                                  laminate=sd.get("laminate"))
                if sd.get("material_angle") is not None:
                    ss.material_angle = float(sd["material_angle"])
            else:
                ss = model.shell_sections.get(body.get("name"))
                if ss is None:
                    return jsonify({"error": "unknown shell section "
                                             f"{body.get('name')!r}"}), 400
            out = _an.summary(ss, mats.get(ss.material), mats)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(out)
