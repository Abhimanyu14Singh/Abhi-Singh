"""HTTP endpoints of Tools > Model History... (core/model_history.py,
core/model_diff.py); registered by ``create_app`` with one line, plus three
one-line hooks in ``server.py`` (auto checkpoint on save, before analysis;
history key on open).  CONTRACT "Model history and visual diff".

``key`` (query or body) names the history; default = the server's
current history key (set by save / open / ``POST /api/history/key``),
else the model name.

* ``GET    /api/history[?key=]``             -> listing
  ``{key, dir, settings, checkpoints: [...newest first], total_bytes}``
* ``POST   /api/history/key``  ``{key}``      -> ``{key}`` (SPA sync)
* ``POST   /api/history/checkpoint`` ``{name?, note?, kind? "manual",
  reason?, model? (default: the server's working model), key?}``
  -> ``{checkpoint, pruned: [ids], skipped}``
* ``GET    /api/history/checkpoint/<id>[?key=]`` -> ``{checkpoint, model}``
* ``PATCH  /api/history/checkpoint/<id>`` ``{name?, note?, pinned?}``
* ``DELETE /api/history/checkpoint/<id>[?key=]`` -> ``{ok}``
* ``POST   /api/history/compare`` ``{a, b, types?, max_items?, tol?}``
  (``a``/``b`` = checkpoint id | ``"current"`` | model dict) -> the
  :func:`skyframe.core.model_diff.diff` dict
* ``POST   /api/history/restore`` ``{from, into? "current", items?,
  apply? false}`` -> ``{model, report, hash}``; without ``items`` the
  whole checkpoint is returned byte-identical; with ``items`` only those
  diff items of ``into`` are reverted.  The result is validated
  (``BuildingModel.from_dict``, 400 with the reason).  ``apply: true``
  also makes it the server's working model.
* ``POST   /api/history/settings`` ``{max_checkpoints?, max_auto?,
  max_bytes?}`` -> ``{settings, pruned}``
"""

from __future__ import annotations

import os
from typing import Any, Dict

from flask import jsonify, request

from skyframe.core import model_diff as _md
from skyframe.core import model_history as _mh


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def history_key(state: Dict[str, Any], explicit: Any = None) -> str:
    """Explicit key, else the tracked key while the working model keeps
    the name it had when the key was set, else the model name."""
    if explicit:
        return _mh.sanitize_key(explicit)
    model = state.get("model")
    name = getattr(model, "name", "") or "Untitled"
    hk = state.get("history_key")
    if isinstance(hk, (list, tuple)) and len(hk) == 2 and hk[1] == name:
        return hk[0]
    return _mh.sanitize_key(name)


def _set_key(state, key: str) -> str:
    k = _mh.sanitize_key(key)
    state["history_key"] = (k, getattr(state.get("model"), "name", ""))
    return k


def _auto_enabled(kind: str) -> bool:
    off = os.environ.get("SKYFRAME_HISTORY_AUTO", "on").lower()
    return off not in ("0", "off", "false", "no")


# ------------------------------------------------------------ server hooks
def on_save(state: Dict[str, Any], name: str) -> None:
    """``POST /api/models/<name>`` hook: key = file name + auto checkpoint
    "save" (never raises)."""
    try:
        k = _set_key(state, name)
        if _auto_enabled("save"):
            _mh.History(k).add(state["model"].to_dict(), kind="auto",
                               reason="save", name=f"Saved as {name}")
    except Exception:                                # pragma: no cover
        pass


def on_open(state: Dict[str, Any], name: str) -> None:
    try:
        _set_key(state, name)
    except Exception:                                # pragma: no cover
        pass


def before_analysis(state: Dict[str, Any]) -> None:
    """``POST /api/analyze`` hook: auto checkpoint "before analysis"
    (deduplicated against the newest checkpoint; never raises)."""
    try:
        if _auto_enabled("analysis"):
            _mh.History(history_key(state)).add(
                state["model"].to_dict(), kind="auto",
                reason="before analysis")
    except Exception:                                # pragma: no cover
        pass


# ------------------------------------------------------------ routes
def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core.model import BuildingModel

    def hist(body=None) -> _mh.History:
        k = (body or {}).get("key") or request.args.get("key")
        return _mh.History(history_key(state, k))

    def err(msg, code=400):
        return jsonify({"error": str(msg)}), code

    @app.get("/api/history")
    def history_list():
        return jsonify(hist().listing())

    @app.post("/api/history/key")
    def history_set_key():
        b = _body()
        if not b.get("key"):
            return err("'key' is required")
        return jsonify({"key": _set_key(state, b["key"])})

    @app.post("/api/history/checkpoint")
    def history_add():
        b = _body()
        md = b.get("model")
        if md is None:
            md = state["model"].to_dict()
        elif not isinstance(md, dict):
            return err("'model' must be a model object")
        kind = b.get("kind", "manual")
        try:
            out = hist(b).add(md, name=b.get("name") or "",
                              note=b.get("note") or "", kind=kind,
                              reason=b.get("reason") or "",
                              dedupe=b.get("dedupe"))
        except ValueError as exc:
            return err(exc)
        return jsonify(out)

    @app.get("/api/history/checkpoint/<cid>")
    def history_get(cid):
        try:
            meta, model = hist().get(cid)
        except (KeyError, OSError) as exc:
            return err(exc, 404)
        return jsonify({"checkpoint": meta, "model": model})

    @app.route("/api/history/checkpoint/<cid>", methods=["PATCH"])
    def history_patch(cid):
        b = _body()
        try:
            meta = hist(b).update(cid, name=b.get("name"),
                                  note=b.get("note"), pinned=b.get("pinned"))
        except KeyError as exc:
            return err(exc, 404)
        return jsonify({"checkpoint": meta})

    @app.delete("/api/history/checkpoint/<cid>")
    def history_delete(cid):
        try:
            ok = hist().delete(cid)
        except KeyError as exc:
            return err(exc, 404)
        if not ok:
            return err(f"no checkpoint {cid!r}", 404)
        return jsonify({"ok": True})

    @app.post("/api/history/settings")
    def history_settings():
        b = _body()
        try:
            return jsonify(hist(b).settings(
                max_checkpoints=b.get("max_checkpoints"),
                max_auto=b.get("max_auto"), max_bytes=b.get("max_bytes")))
        except ValueError as exc:
            return err(exc)

    @app.post("/api/history/compare")
    def history_compare():
        b = _body()
        h = hist(b)
        try:
            cur = state["model"].to_dict()
            a = _mh.resolve(b.get("a"), h, cur)
            bb = _mh.resolve(b.get("b", "current"), h, cur)
            types = b.get("types")
            if types is not None and not isinstance(types, list):
                raise ValueError("'types' must be a list")
            mi = b.get("max_items", 5000)
            if mi is not None and (isinstance(mi, bool)
                                   or not isinstance(mi, int) or mi < 1):
                raise ValueError("'max_items' must be a positive integer")
            out = _md.diff(a, bb, types=types, max_items=mi,
                           tol=float(b.get("tol", 1e-12)))
        except KeyError as exc:
            return err(exc, 404)
        except (ValueError, TypeError) as exc:
            return err(exc)
        return jsonify(out)

    @app.post("/api/history/restore")
    def history_restore():
        b = _body()
        h = hist(b)
        src = b.get("from")
        if src is None:
            return err("'from' (checkpoint id or model) is required")
        try:
            cur = state["model"].to_dict()
            ref = _mh.resolve(src, h, cur)
            items = b.get("items")
            if items is None:
                new, report = ref, {"restored": "all", "skipped": []}
            else:
                if not isinstance(items, list):
                    raise ValueError("'items' must be a list")
                into = _mh.resolve(b.get("into", "current"), h, cur)
                new, report = _md.apply_restore(into, ref, items)
            try:
                model = BuildingModel.from_dict(new)
            except (ValueError, KeyError, TypeError) as exc:
                return err("the restored model is not valid: "
                           f"{exc} (restore the objects it depends on "
                           "too, e.g. sections, materials or patterns)")
        except KeyError as exc:
            return err(exc, 404)
        except (ValueError, TypeError) as exc:
            return err(exc)
        if b.get("apply"):
            state["model"] = model
            new = model.to_dict()
        return jsonify({"model": new, "report": report,
                        "hash": _mh.model_hash(new)})
