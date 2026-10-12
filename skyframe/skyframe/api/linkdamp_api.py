"""HTTP endpoint: material-proportional (composite) modal damping preview
(CONTRACT "Material-proportional (composite) modal damping").  Registered
by ``create_app`` with one line; never mutates the current model.

* ``POST /api/damping/composite`` ``{model?, case?, damping?,
  num_modes?, material_damping?}`` -> ``{default, material_damping,
  modes: [{period, zeta, fractions: {group: share}, energy_check}]}``.
  ``default`` (the ratio of the ``"other"`` group and of materials
  without a ratio) = ``damping`` if given, else the named TH case's
  ``damping``, else 0.05.  ``material_damping`` (optional) overrides the
  model's ratios for the preview.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from flask import jsonify, request

from skyframe.core.model import BuildingModel

_ERRORS = (ValueError, KeyError, TypeError, RuntimeError, ArithmeticError)


def register(app, state: Dict[str, Any]) -> None:

    @app.post("/api/damping/composite")
    def damping_composite():
        from skyframe.core import composite_damping as _cd
        from skyframe.engine import composite_damping as _ecd
        b = request.get_json(silent=True)
        b = b if isinstance(b, dict) else {}
        try:
            model = (BuildingModel.from_dict(b["model"])
                     if b.get("model") is not None else state["model"])
            default = b.get("damping")
            case = b.get("case")
            if default is None and case:
                default = model.th_cases[case].damping
            default = 0.05 if default is None else float(default)
            if b.get("material_damping") is not None:
                model = copy.deepcopy(model)
                model.material_damping = {
                    str(k): float(v)
                    for k, v in dict(b["material_damping"]).items()}
                _cd.validate(model)
            n = b.get("num_modes")
            res = _ecd.composite(model, default,
                                 int(n) if n is not None else None)
        except _ERRORS as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(res)
