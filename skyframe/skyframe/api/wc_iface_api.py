"""HTTP endpoints of the Wave C interface pass (registered by ``create_app``
with one line; CONTRACT "Wave C interface: general grids, explorer, shells
with openings, snapping, units, auto-save, model lock").

* ``GET  /api/units/catalog``  -> ``{kinds: {kind: [{label, si}]},
  quantity_kinds, max_decimals}`` (``core.units_custom``)
* ``POST /api/grid/primary``   ``{name}`` -> model dict: makes grid system
  ``name`` (any kind, general included) the PRIMARY system
  (``grid_systems[0]`` / ``grid``); 404 unknown name
* auto-save (``<models dir>/.autosave/``, a ring of ``AUTOSAVE_KEEP``
  backup versions, newest first):

  - ``POST   /api/autosave``  ``{model, file_name?, reason?}`` -> ``{ok,
    saved_at, hash, versions}``; the model is validated
    (``BuildingModel.from_dict``) but the working model is NOT replaced;
  - ``GET    /api/autosave``  -> ``{exists, keep, current_hash, versions:
    [{version, saved_at, file_name, reason, hash, stories, members,
    shells}]}`` (``current_hash`` = the working model's hash);
  - ``POST   /api/autosave/recover`` ``{version? = 0}`` -> ``{model,
    hash, saved_at, file_name}``; the working model becomes the recovered
    one (its ``hash`` equals the hash recorded when it was saved);
  - ``DELETE /api/autosave`` -> ``{ok, removed}``.

``hash`` = sha256 of ``json.dumps(model.to_dict(), sort_keys=True,
separators=(",", ":"))`` (:func:`model_hash`).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List

from flask import jsonify, request

AUTOSAVE_KEEP = 5
_DIR = ".autosave"


def model_hash(model_dict: dict) -> str:
    """sha256 of the canonical JSON of a model dict."""
    s = json.dumps(model_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def _autosave_dir() -> str:
    from skyframe.api.server import _models_dir
    p = os.path.join(_models_dir(), _DIR)
    os.makedirs(p, exist_ok=True)
    return p


def _slot(k: int) -> str:
    return os.path.join(_autosave_dir(), f"autosave.{k}.json")


def _versions() -> List[dict]:
    out = []
    for k in range(AUTOSAVE_KEEP):
        p = _slot(k)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        m = rec.get("model") or {}
        out.append({"version": k, "saved_at": rec.get("saved_at"),
                    "file_name": rec.get("file_name"),
                    "reason": rec.get("reason", "interval"),
                    "hash": rec.get("hash"),
                    "stories": len(m.get("stories") or []),
                    "members": len(m.get("members") or []),
                    "shells": len(m.get("shells") or [])})
    return out


def register(app, state: Dict[str, Any]) -> None:
    from skyframe.core.model import BuildingModel

    @app.get("/api/units/catalog")
    def units_catalog():
        from skyframe.core import units_custom as _uc
        return jsonify({"kinds": _uc.unit_catalog(),
                        "quantity_kinds": list(_uc.QUANTITY_KINDS),
                        "max_decimals": _uc.MAX_DECIMALS})

    @app.post("/api/grid/primary")
    def grid_primary():
        name = _body().get("name")
        m = state["model"]
        grids = m.effective_grids()
        idx = next((i for i, g in enumerate(grids) if g.name == name), None)
        if idx is None:
            return jsonify({"error": f"unknown grid system {name!r}"}), 404
        if idx:
            d = m.to_dict()
            gs = d["grid_systems"]
            gs.insert(0, gs.pop(idx))
            d["grid"] = gs[0]
            try:
                state["model"] = BuildingModel.from_dict(d)
            except (ValueError, KeyError, TypeError) as exc:
                return jsonify({"error": str(exc)}), 400
        return jsonify(state["model"].to_dict())

    @app.post("/api/autosave")
    def autosave_post():
        body = _body()
        md = body.get("model")
        if not isinstance(md, dict):
            return jsonify({"error": "'model' must be a model object"}), 400
        md = {k: v for k, v in md.items() if k != "_mock_params"}
        try:
            canon = BuildingModel.from_dict(md).to_dict()
        except (ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": f"model is invalid: {exc}"}), 400
        h = model_hash(canon)
        fn = body.get("file_name")
        rec = {"saved_at": time.time(), "hash": h,
               "file_name": fn if isinstance(fn, str) else None,
               "reason": str(body.get("reason") or "interval")[:40],
               "model": canon}
        # rotate the ring (newest = slot 0)
        last = _slot(AUTOSAVE_KEEP - 1)
        if os.path.isfile(last):
            os.remove(last)
        for k in range(AUTOSAVE_KEEP - 2, -1, -1):
            if os.path.isfile(_slot(k)):
                os.replace(_slot(k), _slot(k + 1))
        tmp = _slot(0) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.replace(tmp, _slot(0))
        return jsonify({"ok": True, "saved_at": rec["saved_at"], "hash": h,
                        "versions": len(_versions())})

    @app.get("/api/autosave")
    def autosave_get():
        v = _versions()
        return jsonify({"exists": bool(v), "versions": v,
                        "keep": AUTOSAVE_KEEP,
                        "current_hash": model_hash(
                            state["model"].to_dict())})

    @app.post("/api/autosave/recover")
    def autosave_recover():
        k = _body().get("version", 0)
        if isinstance(k, bool) or not isinstance(k, int) or not (
                0 <= k < AUTOSAVE_KEEP):
            return jsonify({"error": "'version' must be an integer "
                                     f"0..{AUTOSAVE_KEEP - 1}"}), 400
        p = _slot(k)
        if not os.path.isfile(p):
            return jsonify({"error": "no auto-saved model"}), 404
        try:
            with open(p, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
            model = BuildingModel.from_dict(rec["model"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return jsonify({"error": f"auto-save is unreadable: {exc}"}), 400
        state["model"] = model
        out = model.to_dict()
        return jsonify({"model": out, "hash": model_hash(out),
                        "saved_hash": rec.get("hash"),
                        "saved_at": rec.get("saved_at"),
                        "file_name": rec.get("file_name")})

    @app.delete("/api/autosave")
    def autosave_delete():
        n = 0
        for k in range(AUTOSAVE_KEEP):
            if os.path.isfile(_slot(k)):
                os.remove(_slot(k))
                n += 1
        return jsonify({"ok": True, "removed": n})
