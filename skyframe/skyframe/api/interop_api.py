"""HTTP endpoints of the CSI / steel interop formats (CONTRACT "SAP2000 /
SAFE / steel interop"); registered by ``create_app`` with one line.

* ``GET  /api/interop/formats`` -> ``{formats: [{fmt, label, ext,
  export, import, mime}]}``
* ``POST /api/interop/export/<fmt>`` (s2k | f2k | sdnf | idea |
  ifc_revit) body ``{model?, units?, story?, loads_from_above?, results?,
  include?}`` -> ``{ok: true, filename, mime, content | content_b64,
  report}``.  ``f2k`` (column reactions) and ``idea`` (load effects) use
  ``results`` when given, else run the analysis of the model.
* ``POST /api/interop/import/<fmt>`` (s2k | f2k | sdnf | cis2) body
  ``{text | content_b64, units?, sections_from_current? = true, dry_run?,
  soft_errors?}`` -> ``{ok: true, model, warnings, report}``; the model
  replaces the server model unless ``dry_run``.

Errors: 400 ``{error}`` (or 200 ``{ok: false, error}`` with
``soft_errors``).
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict

from flask import jsonify, request

from skyframe.core.model import BuildingModel

FORMATS = {
    "s2k": {"label": "SAP2000 .s2k", "ext": "s2k", "export": True,
            "import": True, "mime": "text/plain"},
    "f2k": {"label": "SAFE floor .f2k", "ext": "f2k", "export": True,
            "import": True, "mime": "text/plain"},
    "sdnf": {"label": "SDNF v3", "ext": "sdnf", "export": True,
             "import": True, "mime": "text/plain"},
    "cis2": {"label": "CIS/2 (LPM6)", "ext": "stp", "export": False,
             "import": True, "mime": "application/x-step"},
    "idea": {"label": "IDEA StatiCa (IOM XML)", "ext": "zip",
             "export": True, "import": False, "mime": "application/zip"},
    "ifc_revit": {"label": "IFC for Revit", "ext": "ifc", "export": True,
                  "import": False, "mime": "application/x-step"},
}


def _summary(model) -> Dict[str, int]:
    return {"stories": len(model.stories), "materials": len(model.materials),
            "frame_sections": len(model.sections),
            "shell_sections": len(model.shell_sections),
            "frames": len(model.members), "areas": len(model.shells),
            "supports": len(model.supports),
            "load_patterns": len(model.patterns),
            "load_cases": len(model.cases),
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

    def _results(model, body: dict):
        if isinstance(body.get("results"), dict):
            return body["results"]
        from skyframe.engine.opensees_engine import OpenSeesEngine
        return OpenSeesEngine(model).run().to_dict()

    @app.get("/api/interop/formats")
    def interop_formats():
        """SAP2000 / SAFE / SDNF / CIS2 / IDEA / Revit-IFC formats."""
        return jsonify({"formats": [{"fmt": k, **v}
                                    for k, v in FORMATS.items()]})

    @app.post("/api/interop/export/<fmt>")
    def interop_export(fmt: str):
        """Export the model (s2k, f2k, sdnf, idea, ifc_revit)."""
        body = _body()
        spec = FORMATS.get(fmt)
        if spec is None or not spec["export"]:
            return _fail(f"unknown export format {fmt!r} ("
                         + "|".join(k for k, v in FORMATS.items()
                                    if v["export"]) + ")", body)
        try:
            model = _model(body)
            name = (model.name or "model").replace("/", "_")
            out: Dict[str, Any]
            if fmt == "s2k":
                from skyframe.io.s2k import export_s2k
                units = tuple(body.get("units") or ("KN", "M"))
                text, rep = export_s2k(model, units=units,
                                       include=body.get("include"))
                out = {"content": text}
            elif fmt == "f2k":
                from skyframe.io.f2k import export_f2k
                units = tuple(body.get("units") or ("KN", "M"))
                above = bool(body.get("loads_from_above", True))
                res = _results(model, body) if above else None
                story = body.get("story") or None
                text, rep = export_f2k(model, story, units=units,
                                       results=res, loads_from_above=above)
                name = f"{name}-{rep['story']}"
                out = {"content": text}
            elif fmt == "sdnf":
                from skyframe.io.sdnf import export_sdnf
                text, rep = export_sdnf(model, units=str(
                    body.get("units") or "m"))
                out = {"content": text}
            elif fmt == "idea":
                from skyframe.io.idea_iom import export_idea_zip
                res = _results(model, body) if body.get(
                    "load_effects", True) else None
                data, rep = export_idea_zip(model, res)
                out = {"content_b64": base64.b64encode(data).decode(
                    "ascii")}
            else:
                from skyframe.io.ifc_write import export_ifc
                text, rep = export_ifc(model, include=body.get("include"),
                                       revit=True)
                rep["format"] = "ifc_revit"
                out = {"content": text}
        except (ValueError, TypeError, KeyError) as exc:
            return _fail(str(exc), body)
        except Exception as exc:                        # noqa: BLE001
            return _fail(f"export failed: {exc}", body)
        return jsonify({"ok": True, "filename": f"{name}.{spec['ext']}",
                        "mime": spec["mime"], "report": rep, **out})

    @app.post("/api/interop/import/<fmt>")
    def interop_import(fmt: str):
        """Import a model (s2k, f2k, sdnf, cis2)."""
        body = _body()
        spec = FORMATS.get(fmt)
        if spec is None or not spec["import"]:
            return _fail(f"unknown import format {fmt!r} ("
                         + "|".join(k for k, v in FORMATS.items()
                                    if v["import"]) + ")", body)
        text = body.get("text")
        if text is None and body.get("content_b64"):
            try:
                text = base64.b64decode(body["content_b64"],
                                        validate=True).decode(
                    "utf-8", errors="replace")
            except (binascii.Error, ValueError):
                return _fail("content_b64 is not valid base64", body)
        if not isinstance(text, str) or not text.strip():
            return _fail("'text' (file contents) is required", body)
        src = state["model"] if body.get("sections_from_current",
                                         True) else None
        try:
            if fmt == "s2k":
                from skyframe.io.s2k import read_s2k
                model, warnings = read_s2k(text)
            elif fmt == "f2k":
                from skyframe.io.f2k import read_f2k
                model, warnings = read_f2k(text)
            elif fmt == "sdnf":
                from skyframe.io.sdnf import read_sdnf
                model, warnings = read_sdnf(text, sections_from=src)
            else:
                from skyframe.io.cis2 import read_cis2
                model, warnings = read_cis2(text, units=body.get("units"),
                                            sections_from=src)
            if not (model.members or model.shells):
                return _fail(f"the {fmt.upper()} file contains no frames "
                             "or areas" + (f" ({warnings[0]})" if warnings
                                           else ""), body)
        except (ValueError, TypeError, KeyError) as exc:
            return _fail(str(exc), body)
        except Exception as exc:                        # noqa: BLE001
            return _fail(f"import failed: {exc}", body)
        if not body.get("dry_run"):
            state["model"] = model
        return jsonify({"ok": True, "model": model.to_dict(),
                        "warnings": warnings,
                        "report": {"format": fmt, "read": _summary(model)}})

    try:                                    # exact shapes in the OpenAPI doc
        from skyframe.api.openapi import _obj, _ref, add_override
        add_override("post", "/api/interop/export/<fmt>", _obj({
            "model": _ref("Model"), "units": {}, "story": {"type": "string"},
            "loads_from_above": {"type": "boolean"},
            "load_effects": {"type": "boolean"},
            "results": _ref("AnalysisResults"),
            "include": {"type": "object"}}), _ref("ExportResponse"),
            "Export SAP2000 / SAFE / SDNF / IDEA / Revit IFC")
        add_override("post", "/api/interop/import/<fmt>", _obj({
            "text": {"type": "string"}, "content_b64": {"type": "string"},
            "units": {"type": "string"},
            "sections_from_current": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "soft_errors": {"type": "boolean"}}), _ref("ImportResponse"),
            "Import SAP2000 / SAFE / SDNF / CIS2")
        add_override("get", "/api/interop/formats", None, _obj({
            "formats": {"type": "array", "items": _obj({
                "fmt": {"type": "string"}, "label": {"type": "string"},
                "ext": {"type": "string"}, "export": {"type": "boolean"},
                "import": {"type": "boolean"},
                "mime": {"type": "string"}})}}, ["formats"]),
            "Interop formats")
    except ImportError:                                 # pragma: no cover
        pass
