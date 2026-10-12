"""Plugin framework: ``plugin.json`` manifests loading JS menu items and
Python hooks from plugin folders (docs/PLUGINS.md, CONTRACT "Plugins").

Folders scanned (first match of an id wins):

* ``$SKYFRAME_PLUGINS_DIR`` (``os.pathsep``-separated list), else
  ``~/.skyframe/plugins`` and the bundled examples (``<repo>/plugins``).

Enabled ids persist in ``$SKYFRAME_PLUGINS_STATE`` (default
``~/.skyframe/plugins_state.json``).  Every plugin starts DISABLED unless
the manifest says ``"enabled_by_default": true``; with no plugin enabled
nothing here touches a model, a result or a table (legacy byte-identical).

Manifest (``plugin.json``)::

    {"id": "example-story-summary",          # [a-z0-9][a-z0-9_-]{0,63}
     "name": "Story Summary", "version": "1.0.0",
     "description": "...", "author": "...",
     "skyframe": ">=1.23",                    # minimum app version
     "python": "plugin.py",                   # optional hook module
     "js": ["plugin.js"],                     # optional ES modules
     "css": ["plugin.css"],                   # optional style sheets
     "hooks": ["pre_analysis", "post_analysis"],
     "endpoints": ["summary"],
     "tables": [{"key": "story_summary", "title": "...",
                 "columns": [{"key", "label", "quantity"}]}],
     "menus": [{"path": "Tools/Plugins", "label": "...", "act": "..."}],
     "permissions": ["model:read", "results:read"],
     "enabled_by_default": false}

Python module contract (all optional, declared in the manifest):

* ``pre_analysis(model, ctx) -> None | [warning str]`` -- before
  ``POST /api/analyze`` builds the engine; may edit ``model`` (like an
  OAPI plugin; declare ``model:write``);
* ``post_analysis(model, results: dict, ctx) -> None | dict`` -- after
  the run; a returned dict is stored at ``results["plugins"][id]``;
* ``endpoints = {name: fn(model, body: dict, ctx) -> JSON}`` -- served
  at ``POST /api/plugins/<id>/call/<name>``;
* ``tables = {key: fn(results: dict, model) -> [row dict]}`` -- added to
  the Show Tables catalogue as ``plugin.<id>.<key>`` (group "Plugins").

``ctx`` = ``{"id", "dir", "manifest", "log": callable}``.

Sandboxing: a plugin is trusted code running in the server process (no
OS sandbox -- see docs/PLUGINS.md).  The framework contains failures: a
bad manifest, an import error or a raising hook marks only that plugin
(``error``), the analysis and every other plugin continue; files are
served only from inside the plugin folder and only with web extensions.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HOOKS = ("pre_analysis", "post_analysis")
PERMISSIONS = ("model:read", "model:write", "results:read", "network",
               "files")
WEB_EXT = {".js": "text/javascript", ".mjs": "text/javascript",
           ".css": "text/css", ".json": "application/json",
           ".svg": "image/svg+xml", ".png": "image/png"}
TABLE_GROUP = "Plugins"
MANIFEST = "plugin.json"
_BUNDLED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..",
                                         "..", "plugins"))


def default_dirs() -> List[str]:
    env = os.environ.get("SKYFRAME_PLUGINS_DIR")
    if env:
        return [d for d in env.split(os.pathsep) if d]
    return [os.path.join(os.path.expanduser("~"), ".skyframe", "plugins"),
            _BUNDLED]


def default_state_path() -> str:
    return os.environ.get("SKYFRAME_PLUGINS_STATE") or os.path.join(
        os.path.expanduser("~"), ".skyframe", "plugins_state.json")


def _version_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3])


def validate_manifest(m: Any, folder: str) -> List[str]:
    """Problems of a manifest dict ([] = valid)."""
    errs: List[str] = []
    if not isinstance(m, dict):
        return ["plugin.json must be a JSON object"]
    pid = m.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        errs.append("id must match [a-z0-9][a-z0-9_-]{0,63}")
    if not isinstance(m.get("name"), str) or not m["name"].strip():
        errs.append("name is required")
    if not isinstance(m.get("version", "0"), str):
        errs.append("version must be a string")
    req = m.get("skyframe")
    if req is not None:
        from skyframe import __version__
        mm = re.match(r"^\s*>=\s*([\d.]+)\s*$", str(req))
        if not mm:
            errs.append("skyframe must look like \">=1.23\"")
        elif _version_tuple(__version__) < _version_tuple(mm.group(1)):
            errs.append(f"needs SkyFrame {req} (this is {__version__})")

    def _file(rel, what):
        if not isinstance(rel, str) or not rel or os.path.isabs(rel) or \
                ".." in rel.replace("\\", "/").split("/"):
            errs.append(f"{what} {rel!r} must be a relative path inside "
                        "the plugin folder")
            return
        if not os.path.isfile(os.path.join(folder, rel)):
            errs.append(f"{what} {rel!r} not found")

    if m.get("python") is not None:
        _file(m["python"], "python")
    for key, ext in (("js", (".js", ".mjs")), ("css", (".css",))):
        files = m.get(key, [])
        if not isinstance(files, list):
            errs.append(f"{key} must be a list")
            continue
        for f in files:
            _file(f, key)
            if isinstance(f, str) and not f.endswith(ext):
                errs.append(f"{key} file {f!r} must end with {ext}")
    for h in m.get("hooks", []) or []:
        if h not in HOOKS:
            errs.append(f"unknown hook {h!r} ({', '.join(HOOKS)})")
    if (m.get("hooks") or m.get("endpoints") or m.get("tables")) and \
            not m.get("python"):
        errs.append("hooks / endpoints / tables need a python module")
    for e in m.get("endpoints", []) or []:
        if not isinstance(e, str) or not ID_RE.match(e):
            errs.append(f"endpoint name {e!r} must match {ID_RE.pattern}")
    from skyframe.core.tables import QUANTITIES
    for t in m.get("tables", []) or []:
        if not isinstance(t, dict) or not ID_RE.match(str(t.get("key", ""))):
            errs.append("each table needs a key matching "
                        f"{ID_RE.pattern}")
            continue
        for c in t.get("columns", []):
            if not isinstance(c, dict) or "key" not in c:
                errs.append(f"table {t['key']}: columns need a key")
            elif c.get("quantity", "text") not in QUANTITIES:
                errs.append(f"table {t['key']}: unknown quantity "
                            f"{c.get('quantity')!r}")
    for p in m.get("permissions", []) or []:
        if p not in PERMISSIONS:
            errs.append(f"unknown permission {p!r}")
    for mn in m.get("menus", []) or []:
        if not isinstance(mn, dict) or not mn.get("label"):
            errs.append("menus entries need a label")
    return errs


class Plugin:
    def __init__(self, folder: str, manifest: dict, errors: List[str]):
        self.dir = folder
        self.manifest = manifest if isinstance(manifest, dict) else {}
        self.id = str(self.manifest.get("id") or os.path.basename(folder))
        self.enabled = False
        self.module = None
        self.error: Optional[str] = "; ".join(errors) if errors else None
        self.invalid = bool(errors)
        self.log: List[str] = []

    def ctx(self) -> dict:
        return {"id": self.id, "dir": self.dir, "manifest": self.manifest,
                "log": lambda msg: self.log.append(str(msg))}

    def to_dict(self) -> dict:
        m = self.manifest
        return {
            "id": self.id, "name": m.get("name", self.id),
            "version": m.get("version", ""),
            "description": m.get("description", ""),
            "author": m.get("author", ""), "dir": self.dir,
            "enabled": self.enabled, "loaded": self.module is not None,
            "error": self.error, "invalid": self.invalid,
            "js": list(m.get("js", []) or []),
            "css": list(m.get("css", []) or []),
            "hooks": list(m.get("hooks", []) or []),
            "endpoints": list(m.get("endpoints", []) or []),
            "tables": [f"plugin.{self.id}.{t['key']}"
                       for t in m.get("tables", []) or []
                       if isinstance(t, dict) and "key" in t],
            "menus": list(m.get("menus", []) or []),
            "permissions": list(m.get("permissions", []) or []),
            "log": self.log[-20:]}


class PluginManager:
    def __init__(self, dirs: Optional[List[str]] = None,
                 state_path: Optional[str] = None):
        self.dirs = list(dirs) if dirs is not None else default_dirs()
        self.state_path = state_path or default_state_path()
        self.plugins: Dict[str, Plugin] = {}
        self.scan()

    # ------------------------------------------------------------ state
    def _read_state(self) -> Dict[str, bool]:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            return {k: bool(v) for k, v in (d.get("enabled") or {}).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _write_state(self) -> None:
        d = {"enabled": {pid: p.enabled for pid, p in
                         sorted(self.plugins.items())}}
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".",
                        exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=1)
        except OSError:
            pass

    # ------------------------------------------------------------ discovery
    def scan(self) -> None:
        for p in list(self.plugins.values()):
            self._unload(p)
        self.plugins = {}
        _purge_plugin_tables()          # a new manager starts from no tables
        for root in self.dirs:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                folder = os.path.join(root, name)
                mf = os.path.join(folder, MANIFEST)
                if not os.path.isfile(mf):
                    continue
                try:
                    with open(mf, "r", encoding="utf-8") as fh:
                        manifest = json.load(fh)
                    errs = validate_manifest(manifest, folder)
                except (OSError, ValueError) as exc:
                    manifest, errs = {}, [f"plugin.json: {exc}"]
                p = Plugin(folder, manifest, errs)
                if p.id in self.plugins:
                    continue                    # first folder wins
                self.plugins[p.id] = p
        state = self._read_state()
        for pid, p in self.plugins.items():
            want = state.get(pid, bool(p.manifest.get("enabled_by_default")))
            if want and not p.invalid:
                self._load(p)

    def list(self) -> List[dict]:
        return [p.to_dict() for p in self.plugins.values()]

    def get(self, pid: str) -> Optional[Plugin]:
        return self.plugins.get(pid)

    def set_enabled(self, pid: str, on: bool) -> dict:
        p = self.plugins.get(pid)
        if p is None:
            raise KeyError(pid)
        if p.invalid and on:
            raise ValueError(f"plugin {pid!r} is invalid: {p.error}")
        if on and not p.enabled:
            self._load(p)
        elif not on and p.enabled:
            self._unload(p)
        self._write_state()
        return p.to_dict()

    # ------------------------------------------------------------ load
    def _load(self, p: Plugin) -> None:
        p.error = None
        rel = p.manifest.get("python")
        if rel:
            path = os.path.join(p.dir, rel)
            modname = "skyframe_plugin_" + re.sub(r"\W", "_", p.id)
            try:
                spec = importlib.util.spec_from_file_location(modname, path)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[modname] = mod
                spec.loader.exec_module(mod)
            except Exception as exc:              # noqa: BLE001
                sys.modules.pop(modname, None)
                p.error = f"import failed: {exc}"
                p.log.append(traceback.format_exc(limit=3))
                p.enabled = False
                return
            p.module = mod
            self._register_tables(p)
        p.enabled = True

    def _unload(self, p: Plugin) -> None:
        self._unregister_tables(p)
        if p.module is not None:
            sys.modules.pop(p.module.__name__, None)
        p.module = None
        p.enabled = False

    def _register_tables(self, p: Plugin) -> None:
        from skyframe.core import tables as _t
        fns = getattr(p.module, "tables", {}) or {}
        for t in p.manifest.get("tables", []) or []:
            fn = fns.get(t["key"])
            if not callable(fn):
                p.error = f"table {t['key']!r}: no tables[{t['key']!r}] " \
                    "function in the python module"
                continue
            cols = [_t._col(c["key"], c.get("label", c["key"]),
                            c.get("quantity", "text"))
                    for c in t.get("columns", [])]
            _t.register_table(f"plugin.{p.id}.{t['key']}",
                              t.get("title", t["key"]), TABLE_GROUP, cols,
                              self._table_fn(p, fn))

    def _table_fn(self, p: Plugin, fn: Callable) -> Callable:
        def run(ctx):
            try:
                return list(fn(ctx.R, ctx.model) or [])
            except Exception as exc:              # noqa: BLE001
                p.error = f"table failed: {exc}"
                ctx.warnings.append(f"plugin {p.id}: {exc}")
                return []
        return run

    @staticmethod
    def _unregister_tables(p: Plugin) -> None:
        from skyframe.core import tables as _t
        keys = {f"plugin.{p.id}.{t['key']}"
                for t in p.manifest.get("tables", []) or []
                if isinstance(t, dict) and "key" in t}
        _t._TABLES[:] = [e for e in _t._TABLES if e[0] not in keys]
        for k in keys:
            _t.TABLES.pop(k, None)
            _t.REGISTERED.discard(k)

    # ------------------------------------------------------------ hooks
    def _active(self, hook: str):
        for p in self.plugins.values():
            if p.enabled and p.module is not None and \
                    hook in (p.manifest.get("hooks") or []) and \
                    callable(getattr(p.module, hook, None)):
                yield p

    def pre_analysis(self, model) -> List[str]:
        """Run the pre hooks; their warnings also go through
        ``warnings.warn`` (so the analysis run log records them)."""
        import warnings as _w
        warns: List[str] = []
        for p in self._active("pre_analysis"):
            try:
                out = p.module.pre_analysis(model, p.ctx())
                warns += [f"plugin {p.id}: {w}" for w in (out or [])]
            except Exception as exc:              # noqa: BLE001
                p.error = f"pre_analysis failed: {exc}"
                warns.append(f"plugin {p.id}: pre_analysis failed: {exc}")
        for w in warns:
            _w.warn(w)
        return warns

    def post_analysis(self, model, results: dict) -> dict:
        outs: Dict[str, Any] = {}
        for p in self._active("post_analysis"):
            try:
                out = p.module.post_analysis(model, results, p.ctx())
                if out is not None:
                    json.dumps(out)                 # must be JSON-safe
                    outs[p.id] = out
            except Exception as exc:              # noqa: BLE001
                p.error = f"post_analysis failed: {exc}"
                outs[p.id] = {"error": str(exc)}
        if outs:
            results.setdefault("plugins", {}).update(outs)
        return results

    def call(self, pid: str, name: str, model, body: dict):
        p = self.plugins.get(pid)
        if p is None or not p.enabled or p.module is None:
            raise KeyError(pid)
        if name not in (p.manifest.get("endpoints") or []):
            raise KeyError(name)
        fn = (getattr(p.module, "endpoints", {}) or {}).get(name)
        if not callable(fn):
            raise KeyError(name)
        return fn(model, body, p.ctx())

    def file_path(self, pid: str, rel: str) -> Optional[str]:
        """Absolute path of a servable plugin file, None when not allowed."""
        p = self.plugins.get(pid)
        if p is None or p.invalid:
            return None
        ext = os.path.splitext(rel)[1].lower()
        if ext not in WEB_EXT:
            return None
        root = os.path.realpath(p.dir)
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return None
        return full


def _purge_plugin_tables() -> None:
    from skyframe.core import tables as _t
    _t._TABLES[:] = [e for e in _t._TABLES if not e[0].startswith("plugin.")]
    for k in [k for k in _t.TABLES if k.startswith("plugin.")]:
        _t.TABLES.pop(k, None)
        _t.REGISTERED.discard(k)


# --------------------------------------------------------------------------- #
# server hooks (one line each in server.py)
# --------------------------------------------------------------------------- #
def manager_of(app) -> Optional[PluginManager]:
    return app.extensions.get("skyframe_plugins") if app else None


def pre_analysis(app, model) -> List[str]:
    mgr = manager_of(app)
    return mgr.pre_analysis(model) if mgr else []


def post_analysis(app, model, d: dict) -> dict:
    mgr = manager_of(app)
    return mgr.post_analysis(model, d) if mgr else d
