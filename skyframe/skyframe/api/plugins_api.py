"""HTTP endpoints of the plugin framework (CONTRACT "Plugins"); registered
by ``create_app`` with one line.

* ``GET  /api/plugins`` -> ``{plugins: [Plugin], dirs, state_path}``
* ``POST /api/plugins/rescan`` -> same (re-reads every folder)
* ``POST /api/plugins/<id>/enable`` ``{enabled: bool}`` -> ``Plugin``
* ``GET  /api/plugins/<id>/files/<path>`` -> a JS / CSS / JSON / SVG /
  PNG file from inside the plugin folder (404 otherwise)
* ``POST /api/plugins/<id>/call/<name>`` ``{...}`` -> the endpoint's JSON
  (404 unless the plugin is enabled and declares the endpoint)

Plugin tables are ordinary results tables (``POST /api/tables/plugin.<id>.
<key>``); the analysis hooks run inside ``POST /api/analyze``.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import jsonify, request, send_file

from skyframe.core.plugins import WEB_EXT, PluginManager


def register(app, state: Dict[str, Any]) -> None:
    mgr = PluginManager()
    app.extensions["skyframe_plugins"] = mgr

    def _listing():
        return {"plugins": mgr.list(), "dirs": mgr.dirs,
                "state_path": mgr.state_path}

    @app.get("/api/plugins")
    def plugins_list():
        """Installed plugins and their state."""
        return jsonify(_listing())

    @app.post("/api/plugins/rescan")
    def plugins_rescan():
        """Re-read every plugins folder."""
        mgr.scan()
        return jsonify(_listing())

    @app.post("/api/plugins/<pid>/enable")
    def plugins_enable(pid: str):
        """Enable / disable one plugin (persisted)."""
        body = request.get_json(silent=True) or {}
        on = body.get("enabled", True)
        if not isinstance(on, bool):
            return jsonify({"error": "'enabled' must be true or false"}), 400
        try:
            return jsonify(mgr.set_enabled(pid, on))
        except KeyError:
            return jsonify({"error": f"unknown plugin {pid!r}"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/plugins/<pid>/files/<path:fname>")
    def plugins_file(pid: str, fname: str):
        """A web asset from inside the plugin folder."""
        path = mgr.file_path(pid, fname)
        if path is None:
            return jsonify({"error": "not found"}), 404
        import os
        resp = send_file(path, mimetype=WEB_EXT[os.path.splitext(
            path)[1].lower()], max_age=0)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    @app.post("/api/plugins/<pid>/call/<name>")
    def plugins_call(pid: str, name: str):
        """Run a plugin's custom endpoint on the current model."""
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"error": "body must be a JSON object"}), 400
        try:
            out = mgr.call(pid, name, state["model"], body)
        except KeyError:
            return jsonify({"error": f"no endpoint {name!r} on an enabled "
                                     f"plugin {pid!r}"}), 404
        except Exception as exc:                      # noqa: BLE001
            p = mgr.get(pid)
            if p is not None:
                p.error = f"endpoint {name} failed: {exc}"
            return jsonify({"error": f"plugin {pid}: {exc}"}), 400
        return jsonify(out)

    try:                                    # exact shapes in the OpenAPI doc
        from skyframe.api.openapi import _obj, _ref, add_override
        listing = _obj({"plugins": {"type": "array",
                                    "items": _ref("Plugin")},
                        "dirs": {"type": "array",
                                 "items": {"type": "string"}},
                        "state_path": {"type": "string"}},
                       ["plugins", "dirs", "state_path"])
        add_override("get", "/api/plugins", None, listing, "Installed plugins")
        add_override("post", "/api/plugins/rescan", None, listing,
                     "Re-read the plugins folders")
        add_override("post", "/api/plugins/<pid>/enable",
                     _obj({"enabled": {"type": "boolean"}}), _ref("Plugin"),
                     "Enable / disable a plugin")
        add_override("post", "/api/plugins/<pid>/call/<name>",
                     _obj({}), _obj({}), "Call a plugin endpoint")
    except ImportError:                                 # pragma: no cover
        pass
