"""Named model checkpoints on disk (Tools > Model History).

A history is a folder ``<root>/<key>/`` holding ``index.json`` and one
gzip file per checkpoint (``<id>.json.gz``: the model dict as canonical
JSON, ``json.dumps(model, separators=(",", ":"))`` -- the bytes are kept
exactly, so a restore is byte-identical).  ``root`` defaults to
``<models dir>/.history`` (the saved-model folder, i.e. the desktop app
data folder ``~/.skyframe/models`` unless ``SKYFRAME_MODELS_DIR`` moves it);
``SKYFRAME_HISTORY_DIR`` overrides it.  ``key`` is the saved model's file
name (or the model name for an unsaved model), sanitised.

Checkpoints are ``manual`` (named by the user; kept until deleted unless
the hard limits force pruning) or ``auto`` (on save, before analysis,
before bulk edits; a ring of ``max_auto``).  An auto checkpoint whose
model hash equals the newest checkpoint's is not written (no duplicates).
Pruning (after every write): oldest unpinned auto checkpoints beyond
``max_auto``; then, while the count exceeds ``max_checkpoints`` or the
stored bytes exceed ``max_bytes``, the oldest unpinned auto, then the
oldest unpinned manual checkpoint.  Pinned checkpoints are never pruned.

CONTRACT "Model history and visual diff".
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import model_diff as _md

DEFAULTS = {"max_checkpoints": 100, "max_auto": 30,
            "max_bytes": 256 * 1024 * 1024}
LIMITS = {"max_checkpoints": (2, 10000), "max_auto": (0, 10000),
          "max_bytes": (64 * 1024, 64 * 1024 ** 3)}
KINDS = ("manual", "auto")
_KEY_RE = re.compile(r"[^A-Za-z0-9 _.-]+")
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_LOCK = threading.RLock()


def sanitize_key(key: Any) -> str:
    k = _KEY_RE.sub("_", str(key or "").strip())[:80].strip(" .")
    return k or "Untitled"


def canonical_bytes(model: dict) -> bytes:
    return json.dumps(model, separators=(",", ":")).encode("utf-8")


def model_hash(model: dict) -> str:
    """sha256 of the sorted-key compact JSON (order-independent)."""
    s = json.dumps(model, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def default_root() -> str:
    env = os.environ.get("SKYFRAME_HISTORY_DIR")
    if env:
        return env
    models = os.environ.get("SKYFRAME_MODELS_DIR") or os.path.join(
        os.path.expanduser("~"), ".skyframe", "models")
    return os.path.join(models, ".history")


class History:
    """One model's checkpoint folder."""

    def __init__(self, key: str, root: Optional[str] = None):
        self.key = sanitize_key(key)
        self.root = root or default_root()
        self.dir = os.path.join(self.root, self.key)

    # ------------------------------------------------------------ index
    def _index_path(self) -> str:
        return os.path.join(self.dir, "index.json")

    def _load(self) -> dict:
        try:
            with open(self._index_path(), "r", encoding="utf-8") as fh:
                idx = json.load(fh)
            if not isinstance(idx, dict):
                raise ValueError
        except (OSError, ValueError):
            idx = {}
        idx.setdefault("key", self.key)
        idx.setdefault("checkpoints", [])
        st = dict(DEFAULTS)
        st.update({k: v for k, v in (idx.get("settings") or {}).items()
                   if k in DEFAULTS})
        idx["settings"] = st
        return idx

    def _save(self, idx: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(idx, fh, indent=1)
        os.replace(tmp, self._index_path())

    def _file(self, cid: str) -> str:
        if not _ID_RE.fullmatch(str(cid)):
            raise KeyError(f"bad checkpoint id {cid!r}")
        return os.path.join(self.dir, f"{cid}.json.gz")

    # ------------------------------------------------------------ public
    def listing(self) -> dict:
        with _LOCK:
            idx = self._load()
        cps = sorted(idx["checkpoints"], key=lambda c: c["created"],
                     reverse=True)
        return {"key": self.key, "dir": self.dir,
                "settings": idx["settings"], "checkpoints": cps,
                "total_bytes": sum(c.get("bytes", 0) for c in cps)}

    def settings(self, **kw) -> dict:
        with _LOCK:
            idx = self._load()
            for k, v in kw.items():
                if k not in DEFAULTS or v is None:
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ValueError(f"{k} must be a number")
                lo, hi = LIMITS[k]
                if not lo <= int(v) <= hi:
                    raise ValueError(f"{k} must be in [{lo}, {hi}]")
                idx["settings"][k] = int(v)
            pruned = self._prune(idx)
            self._save(idx)
        return {"settings": idx["settings"], "pruned": pruned}

    def add(self, model: dict, name: str = "", note: str = "",
            kind: str = "manual", reason: str = "",
            dedupe: Optional[bool] = None) -> dict:
        """Write a checkpoint; returns ``{checkpoint, pruned, skipped}``.

        ``dedupe`` (default: True for auto checkpoints) skips the write
        when the model equals the newest checkpoint."""
        if not isinstance(model, dict):
            raise ValueError("model must be a JSON object")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        model = {k: v for k, v in model.items() if k != "_mock_params"}
        raw = canonical_bytes(model)
        h = model_hash(model)
        if dedupe is None:
            dedupe = kind == "auto"
        with _LOCK:
            idx = self._load()
            if dedupe and idx["checkpoints"]:
                newest = max(idx["checkpoints"], key=lambda c: c["created"])
                if newest.get("hash") == h:
                    return {"checkpoint": newest, "pruned": [],
                            "skipped": True}
            cid = uuid.uuid4().hex[:12]
            os.makedirs(self.dir, exist_ok=True)
            gz = gzip.compress(raw, compresslevel=6, mtime=0)
            tmp = self._file(cid) + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(gz)
            os.replace(tmp, self._file(cid))
            created = time.time()
            if idx["checkpoints"]:     # strictly increasing timestamps
                created = max(created, max(
                    c["created"] for c in idx["checkpoints"]) + 1e-6)
            cp = {"id": cid, "name": str(name or "")[:120],
                  "note": str(note or "")[:2000], "kind": kind,
                  "reason": str(reason or "")[:80], "created": created,
                  "hash": h, "bytes": len(gz), "raw_bytes": len(raw),
                  "pinned": False, "model_name": str(model.get("name", "")),
                  "counts": _md.model_counts(model)}
            idx["checkpoints"].append(cp)
            pruned = self._prune(idx, keep=cid)
            self._save(idx)
        return {"checkpoint": cp, "pruned": pruned, "skipped": False}

    def get(self, cid: str) -> Tuple[dict, dict]:
        """``(meta, model dict)``; KeyError when unknown."""
        with _LOCK:
            idx = self._load()
            meta = next((c for c in idx["checkpoints"] if c["id"] == cid),
                        None)
            if meta is None:
                raise KeyError(f"no checkpoint {cid!r}")
            with open(self._file(cid), "rb") as fh:
                raw = gzip.decompress(fh.read())
        return meta, json.loads(raw.decode("utf-8"))

    def raw(self, cid: str) -> bytes:
        with open(self._file(cid), "rb") as fh:
            return gzip.decompress(fh.read())

    def update(self, cid: str, **kw) -> dict:
        with _LOCK:
            idx = self._load()
            meta = next((c for c in idx["checkpoints"] if c["id"] == cid),
                        None)
            if meta is None:
                raise KeyError(f"no checkpoint {cid!r}")
            if kw.get("name") is not None:
                meta["name"] = str(kw["name"])[:120]
            if kw.get("note") is not None:
                meta["note"] = str(kw["note"])[:2000]
            if kw.get("pinned") is not None:
                meta["pinned"] = bool(kw["pinned"])
            if kw.get("kind") in KINDS:
                meta["kind"] = kw["kind"]
            self._save(idx)
        return meta

    def delete(self, cid: str) -> bool:
        with _LOCK:
            idx = self._load()
            n0 = len(idx["checkpoints"])
            idx["checkpoints"] = [c for c in idx["checkpoints"]
                                  if c["id"] != cid]
            if len(idx["checkpoints"]) == n0:
                return False
            try:
                os.remove(self._file(cid))
            except OSError:
                pass
            self._save(idx)
        return True

    # ------------------------------------------------------------ pruning
    def _prune(self, idx: dict, keep: Optional[str] = None) -> List[str]:
        st = idx["settings"]
        cps = sorted(idx["checkpoints"], key=lambda c: c["created"])
        removed: List[str] = []

        def drop(c):
            cps.remove(c)
            removed.append(c["id"])
            try:
                os.remove(self._file(c["id"]))
            except OSError:
                pass

        autos = [c for c in cps if c["kind"] == "auto" and not c.get(
            "pinned") and c["id"] != keep]
        excess = sum(1 for c in cps if c["kind"] == "auto"
                     and not c.get("pinned")) - st["max_auto"]
        for c in autos[:max(0, excess)]:
            drop(c)

        def over():
            return (len(cps) > st["max_checkpoints"]
                    or sum(c.get("bytes", 0) for c in cps) > st["max_bytes"])

        for kind in ("auto", "manual"):
            for c in [c for c in list(cps) if c["kind"] == kind
                      and not c.get("pinned") and c["id"] != keep]:
                if not over():
                    break
                drop(c)
        idx["checkpoints"] = cps
        return removed


def resolve(model_or_ref, history: History, current: dict) -> dict:
    """A compare / restore side: ``"current"`` -> ``current``; a checkpoint
    id -> that checkpoint's model; a dict -> itself (``{"model": {...}}``
    wrappers unwrapped)."""
    if model_or_ref == "current" or model_or_ref is None:
        return current
    if isinstance(model_or_ref, dict):
        m = model_or_ref.get("model") if isinstance(
            model_or_ref.get("model"), dict) else model_or_ref
        return {k: v for k, v in m.items() if k != "_mock_params"}
    return history.get(str(model_or_ref))[1]
