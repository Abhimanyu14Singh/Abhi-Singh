"""HTTP endpoints of the Rhino / Grasshopper exchange (CONTRACT "Rhino /
Grasshopper exchange"); registered by ``create_app`` with one line.

Spec endpoints (shared with the Grasshopper plugin):

* ``GET    /api/exchange/status``
* ``POST   /api/exchange/preview  {exchange, mapping?, mode?, model?}``
* ``POST   /api/exchange/apply    {exchange, mapping?, mode?, run?, model?}``
* ``GET    /api/exchange/results?session=&case=&what=``
* ``POST   /api/rhino/import``  multipart ``file`` (.3dm) or ``{path}``
* ``POST|GET|DELETE /api/rhino/link``

UI helpers (optional additions): ``GET /api/rhino/info``,
``GET|POST /api/exchange/mappings``, ``POST /api/exchange/sessions/remove``.

``model`` (optional) in a body = the UI's current model (as for
``/api/edit``); the result then replaces the stored model.  CORS: only
``http(s)://127.0.0.1|localhost[:port]`` origins get the allow headers.
"""

from __future__ import annotations

import os
import re
import threading
import time
import warnings as _warnings
from typing import Any, Dict

from flask import jsonify, request

_ORIGIN_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])"
                        r"(:\d{1,5})?$")
LINK_POLL_S = 1.0


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("skyframe")
    except Exception:                                   # pragma: no cover
        return "1.21.0"


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core import rhino_exchange as X
    from skyframe.core.model import BuildingModel
    from skyframe.io import rhino3dm_io as R

    lock = threading.RLock()
    state.setdefault("exchange_rev", 0)

    # ------------------------------------------------------------ CORS
    @app.after_request
    def rhx__exchange_cors(resp):
        origin = request.headers.get("Origin")
        if origin and _ORIGIN_RE.match(origin) and \
                request.path.startswith("/api/"):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Methods"] = \
                "GET, POST, DELETE, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Access-Control-Max-Age"] = "600"
        return resp

    def _body() -> dict:
        b = request.get_json(silent=True)
        return b if isinstance(b, dict) else {}

    def _base(body):
        if isinstance(body.get("model"), dict):
            return BuildingModel.from_dict(body["model"])
        return state["model"]

    def _err(msg, code=400):
        return jsonify({"error": str(msg).strip("'\"")}), code

    def _has_results(model) -> bool:
        from skyframe.api import server as _srv
        res, _ctx = _srv._tables_lookup(model)
        return res is not None

    def _run(model) -> dict:
        from skyframe.api import server as _srv
        if not _srv._OPENSEES_OK:
            raise RuntimeError("OpenSeesPy is not available")
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            engine = _srv.OpenSeesEngine(model)
            res = engine.run().to_dict()
        _srv._tables_remember(res, engine)
        return {"ok": True,
                "case_status": res.get("case_status") or {},
                "combo_status": res.get("combo_status") or {},
                "warnings": [str(w.message) for w in caught][:50]}

    def _link_info() -> dict:
        ln = state.get("rhino_link")
        if not ln:
            return {"linked": False, "path": None, "mtime": None,
                    "changed": False}
        try:
            cur = os.path.getmtime(ln["path"])
        except OSError:
            cur = None
        return {"linked": True, "path": ln["path"], "mtime": ln["mtime"],
                "current_mtime": cur, "exists": cur is not None,
                "changed": cur is not None and cur != ln["mtime"],
                "session": ln["session"], "mapping": ln.get("mapping"),
                "auto_apply": bool(ln.get("auto_apply")),
                "last_apply": ln.get("last_apply"),
                "last_error": ln.get("last_error"),
                "revision": state["exchange_rev"]}

    def _apply(model, payload, mapping, mode):
        new, rep = X.apply_exchange(model, payload, mapping, mode)
        with lock:
            state["model"] = new
            state["exchange_rev"] += 1
            ln = state.get("rhino_link")
            if ln and rep["session"] == ln["session"] and \
                    ln.get("read_mtime") is not None:
                ln["mtime"] = ln.pop("read_mtime")
                ln["last_apply"] = time.time()
        return new, rep

    # ------------------------------------------------------- spec API
    @app.get("/api/exchange/status")
    def rhx_exchange_status():
        model = state["model"]
        return jsonify({"app": "SkyFrame", "version": _version(),
                        "schema": X.SCHEMA, "model": model.name,
                        "sessions": X.sessions(model),
                        "analysis": {"has_results": _has_results(model)},
                        "rhino3dm": {"available": R.available(),
                                     "version": R.version()},
                        "revision": state["exchange_rev"],
                        "link": _link_info()})

    @app.post("/api/exchange/preview")
    def rhx_exchange_preview():
        b = _body()
        if not isinstance(b.get("exchange"), dict):
            return _err("'exchange' (a skyframe.exchange/1 payload) is "
                        "required")
        try:
            out = X.preview_exchange(_base(b), b["exchange"],
                                     b.get("mapping"),
                                     b.get("mode") or "merge")
        except (ValueError, KeyError, TypeError) as exc:
            return _err(exc)
        return jsonify(out)

    @app.post("/api/exchange/apply")
    def rhx_exchange_apply():
        b = _body()
        if not isinstance(b.get("exchange"), dict):
            return _err("'exchange' (a skyframe.exchange/1 payload) is "
                        "required")
        try:
            new, rep = _apply(_base(b), b["exchange"], b.get("mapping"),
                              b.get("mode") or "merge")
        except (ValueError, KeyError, TypeError) as exc:
            return _err(exc)
        out = {"report": rep, "model_summary": rep.pop("model_summary"),
               "model": new.to_dict(), "revision": state["exchange_rev"]}
        if b.get("run"):
            try:
                out["analysis"] = _run(new)
            except Exception as exc:
                out["analysis"] = {"ok": False, "error": str(exc)}
        return jsonify(out)

    @app.get("/api/exchange/results")
    def rhx_exchange_results():
        a = request.args
        model = state["model"]
        from skyframe.api import server as _srv
        res, _ctx = _srv._tables_lookup(model)
        if res is None:
            return _err("no analysis results for the current model -- run "
                        "the analysis (POST /api/analyze, or apply with "
                        "run: true)", 409)
        try:
            return jsonify(X.results_by_source(
                model, res, a.get("session") or None, a.get("case") or None,
                a.get("what") or "displacements"))
        except KeyError as exc:
            return _err(exc, 404)
        except ValueError as exc:
            return _err(exc)

    # ------------------------------------------------- UI helpers
    @app.get("/api/exchange/mappings")
    def rhx_exchange_mappings_get():
        return jsonify({"mappings": dict(state["model"].exchange_mappings)})

    @app.post("/api/exchange/mappings")
    def rhx_exchange_mappings_post():
        """``{name, profile: {rules}|null, model?}`` -> ``{mappings,
        model}`` (null deletes the profile)."""
        b = _body()
        try:
            model = BuildingModel.from_dict(_base(b).to_dict())
            X.save_mapping(model, b.get("name"), b.get("profile"))
            model.validate()
        except (ValueError, KeyError, TypeError) as exc:
            return _err(exc)
        state["model"] = model
        return jsonify({"mappings": dict(model.exchange_mappings),
                        "model": model.to_dict()})

    @app.post("/api/exchange/sessions/remove")
    def rhx_exchange_session_remove():
        b = _body()
        try:
            new, rep = X.remove_session(_base(b), str(b.get("session")))
        except (ValueError, KeyError, TypeError) as exc:
            return _err(exc)
        state["model"] = new
        state["exchange_rev"] += 1
        ln = state.get("rhino_link")
        if ln and ln["session"] == rep["session"]:
            state.pop("rhino_link", None)
        return jsonify({"report": rep, "model": new.to_dict()})

    @app.get("/api/rhino/info")
    def rhx_rhino_info():
        return jsonify({"available": R.available(), "version": R.version(),
                        "hint": None if R.available() else R.INSTALL_HINT})

    @app.post("/api/rhino/import")
    def rhx_rhino_import():
        try:
            if "file" in request.files:
                fs = request.files["file"]
                data = fs.read()
                if not data:
                    return _err("empty file")
                payload = R.read_3dm(data, doc=fs.filename or "upload.3dm",
                                     session=request.form.get("session")
                                     or None)
                mtime = None
            else:
                b = _body()
                path = b.get("path")
                if not isinstance(path, str) or not path.strip():
                    return _err("multipart 'file' or JSON {path} required")
                path = os.path.abspath(os.path.expanduser(path.strip()))
                mtime = os.path.getmtime(path) if os.path.isfile(path) \
                    else None
                payload = R.read_3dm(path)
                ln = state.get("rhino_link")
                if ln and ln["path"] == path:
                    ln["read_mtime"] = mtime
        except R.RhinoUnavailable as exc:
            return _err(exc, 501)
        except (ValueError, OSError) as exc:
            return _err(exc)
        return jsonify({"exchange": payload,
                        "layers": R.layer_table(payload),
                        "warnings": payload.get("warnings") or [],
                        "mtime": mtime})

    def _poll_loop():
        while True:
            time.sleep(LINK_POLL_S)
            ln = state.get("rhino_link")
            if not ln or not ln.get("auto_apply"):
                if not ln:
                    state.pop("_rhino_poller", None)
                    return
                continue
            try:
                cur = os.path.getmtime(ln["path"])
            except OSError:
                continue
            if cur == ln["mtime"]:
                continue
            try:
                payload = R.read_3dm(ln["path"])
                ln["read_mtime"] = cur
                _apply(state["model"], payload, ln.get("mapping"), "merge")
                ln["last_error"] = None
            except Exception as exc:                    # keep polling
                ln["mtime"] = cur
                ln["last_error"] = str(exc)

    @app.route("/api/rhino/link", methods=["GET", "POST", "DELETE"])
    def rhx_rhino_link():
        if request.method == "GET":
            return jsonify(_link_info())
        if request.method == "DELETE":
            state.pop("rhino_link", None)
            return jsonify(_link_info())
        b = _body()
        path = b.get("path")
        if not isinstance(path, str) or not path.strip():
            return _err("'path' (a .3dm file path) is required")
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if not os.path.isfile(path):
            return _err(f"file not found: {path}", 404)
        if not R.available():
            return _err(R.INSTALL_HINT, 501)
        mapping = b.get("mapping")
        if mapping is not None and not isinstance(mapping, (str, dict)):
            return _err("mapping must be a profile name or {rules}")
        state["rhino_link"] = {
            "path": path, "session": R.session_for(path),
            "mtime": os.path.getmtime(path), "mapping": mapping,
            "auto_apply": bool(b.get("auto_apply")), "last_apply": None,
            "last_error": None}
        if b.get("auto_apply") and not state.get("_rhino_poller"):
            th = threading.Thread(target=_poll_loop, daemon=True,
                                  name="skyframe-rhino-link")
            state["_rhino_poller"] = th
            th.start()
        return jsonify(_link_info())
