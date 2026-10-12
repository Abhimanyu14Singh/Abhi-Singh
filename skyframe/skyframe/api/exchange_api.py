"""HTTP endpoints of the model exchange (E2K / IFC / DXF / Excel);
registered by ``create_app`` with one line (CONTRACT "Model exchange: E2K /
IFC / DXF / Excel").

* ``POST /api/exchange/export/<fmt>`` (fmt = e2k | ifc | dxf | xlsx)
  body ``{model?, units?, include?, extensions?, unit_scale?, tables?}`` ->
  ``{ok: true, filename, mime, content | content_b64, report}``.
* ``POST /api/exchange/import/<fmt>`` body ``{text | content_b64, units?,
  unit_scale?, sections_from_current?, layer_map?, model?, dry_run?,
  soft_errors?}`` -> ``{ok: true, model, warnings, report}``; the imported
  model REPLACES the server model unless ``dry_run``.  Excel import edits
  the base model (``model`` or the current one) through the model tables.

Errors: 400 ``{error}`` (or 200 ``{ok: false, error}`` with
``soft_errors: true``).
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict

from flask import jsonify, request

from skyframe.core.model import BuildingModel

FORMATS = ("e2k", "ifc", "dxf", "xlsx")
MIME = {"e2k": "text/plain", "ifc": "application/x-step",
        "dxf": "application/dxf",
        "xlsx": "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"}


def _summary(model) -> Dict[str, int]:
    return {"stories": len(model.stories), "materials": len(model.materials),
            "frame_sections": len(model.sections),
            "shell_sections": len(model.shell_sections),
            "frames": len(model.members), "areas": len(model.shells),
            "links": len(model.links),
            "load_patterns": len(model.patterns),
            "load_cases": (len(model.cases) + len(model.rs_cases)
                           + len(model.th_cases)
                           + len(model.buckling_cases)),
            "load_combinations": len(model.combos),
            "groups": len(model.groups)}


def register(app, state: Dict[str, Any]) -> None:

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _fail(msg: str, body: dict):
        if body.get("soft_errors"):
            return jsonify({"ok": False, "error": msg})
        return jsonify({"error": msg}), 400

    def _model(body: dict):
        if body.get("model") is not None:
            return BuildingModel.from_dict(body["model"])
        return state["model"]

    @app.post("/api/exchange/export/<fmt>")
    def exchange_export(fmt: str):
        body = _body()
        if fmt not in FORMATS:
            return _fail(f"unknown format {fmt!r} ({'|'.join(FORMATS)})",
                         body)
        try:
            model = _model(body)
            name = (model.name or "model").replace("/", "_")
            inc = body.get("include") if isinstance(body.get("include"),
                                                    dict) else None
            if fmt == "e2k":
                from skyframe.io.e2k_write import export_e2k
                units = body.get("units") or ("KN", "M")
                if isinstance(units, list):
                    units = tuple(units)
                text, rep = export_e2k(model, units=units, include=inc,
                                       extensions=bool(
                                           body.get("extensions", True)))
                out = {"content": text}
            elif fmt == "ifc":
                from skyframe.io.ifc_write import export_ifc
                text, rep = export_ifc(model, include=inc)
                out = {"content": text}
            elif fmt == "dxf":
                from skyframe.io.dxf_layers import export_dxf
                text, rep = export_dxf(model, include=inc, unit_scale=float(
                    body.get("unit_scale", 1.0)))
                out = {"content": text}
            else:
                from skyframe.io.xlsx import export_xlsx
                tabs = body.get("tables")
                data, rep = export_xlsx(model, tables=tabs if isinstance(
                    tabs, list) else None)
                out = {"content_b64": base64.b64encode(data).decode("ascii")}
        except (ValueError, TypeError, KeyError) as exc:
            return _fail(str(exc), body)
        return jsonify({"ok": True, "filename": f"{name}.{fmt}",
                        "mime": MIME[fmt], "report": rep, **out})

    @app.post("/api/exchange/import/<fmt>")
    def exchange_import(fmt: str):
        body = _body()
        if fmt not in FORMATS:
            return _fail(f"unknown format {fmt!r} ({'|'.join(FORMATS)})",
                         body)
        text = body.get("text")
        raw = None
        if body.get("content_b64"):
            try:
                raw = base64.b64decode(body["content_b64"], validate=True)
            except (binascii.Error, ValueError):
                return _fail("content_b64 is not valid base64", body)
            if fmt != "xlsx" and text is None:
                text = raw.decode("utf-8", errors="replace")
        try:
            if fmt == "xlsx":
                if raw is None:
                    return _fail("Excel import needs 'content_b64'", body)
                from skyframe.io.xlsx import import_xlsx
                base = _model(body)
                model, errors, summary = import_xlsx(base, raw)
                if errors:
                    first = errors[0]
                    where = f" [{first['table']}]" if first.get("table") \
                        else ""
                    out = {"ok": False, "error": f"{len(errors)} error(s): "
                           f"{first['message']}{where}", "errors": errors}
                    if body.get("soft_errors"):
                        return jsonify(out)
                    return jsonify(out), 400
                warnings = []
                report = {"format": "xlsx", "tables": summary,
                          "read": _summary(model)}
            else:
                if not isinstance(text, str) or not text.strip():
                    return _fail("'text' (file contents) is required", body)
                if fmt == "e2k":
                    from skyframe.io.e2k_read import read_e2k
                    units = body.get("units") or ("KN", "M")
                    model, warnings = read_e2k(text, units=tuple(units))
                elif fmt == "ifc":
                    from skyframe.io.ifc_read import read_ifc
                    model, warnings = read_ifc(text)
                else:
                    from skyframe.io.dxf_layers import import_dxf_layers
                    src = state["model"] if body.get(
                        "sections_from_current", True) else None
                    model, warnings = import_dxf_layers(
                        text, sections_from=src,
                        unit_scale=float(body.get("unit_scale", 1.0)),
                        layer_map=body.get("layer_map"))
                if not (model.members or model.shells or model.links):
                    return _fail(f"the {fmt.upper()} file contains no "
                                 "frames, areas or links"
                                 + (f" ({warnings[0]})" if warnings else ""),
                                 body)
                report = {"format": fmt, "read": _summary(model),
                          "extensions": text.count("\n$SF ")
                          if fmt == "e2k" else 0}
        except (ValueError, TypeError, KeyError) as exc:
            return _fail(str(exc), body)
        except Exception as exc:                     # noqa: BLE001
            return _fail(f"import failed: {exc}", body)
        if not body.get("dry_run"):
            state["model"] = model
        return jsonify({"ok": True, "model": model.to_dict(),
                        "warnings": warnings, "report": report})
