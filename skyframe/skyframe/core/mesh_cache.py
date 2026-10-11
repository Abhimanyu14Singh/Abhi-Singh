"""Cross-run mesh cache (geometry unchanged -> no re-meshing).

CONTRACT "Analysis performance, solver policy and convergence strategy".

:func:`mesh_model` is a deterministic pure function of the model, and on
meshed buildings it is seconds of pure Python (4.5 s for a 20-story 5x5-bay
model with 1.5 m slab/wall meshes).  The engine meshes once per
``OpenSeesEngine``; the server creates a fresh engine per request, so an
unchanged model used to be re-meshed by every ``/api/analyze``,
``/api/tables/*`` (cold), ``/api/analyze/*`` call.  This module keeps the
last :data:`CAPACITY` meshes keyed by a SHA-256 of the model's pickled
state with the case-definition attributes blanked (cases / combos / RS /
TH / pushover / staged / ... definitions, run flags and analysis options
cannot influence the mesh; load PATTERNS stay in the key — nodal and
member point loads do place mesh points).  A hit returns a fresh
unpickled copy (pickle round trips floats exactly) re-attached to the live
model (``MeshedModel.model``), so the engine gets an object bit-identical
to a new :func:`mesh_model` result and no caller can mutate the cached
one.  Any model change that could matter — or a change of a mesher tuning
constant (UPPER_CASE module scalars of core.mesh / framemesh / polymesh) —
changes the key; an unpicklable model simply bypasses the cache.
``SKYFRAME_SLOW_PATH=1`` or ``SKYFRAME_MESH_CACHE=0`` disables it.
"""

from __future__ import annotations

import copy
import hashlib
import os
import pickle
import threading
from collections import OrderedDict
from typing import Optional

from skyframe.core.mesh import MeshedModel, mesh_model

CAPACITY = 2

# BuildingModel attributes that never influence meshing
EXCLUDE = ("name", "cases", "combos", "rs_cases", "rs_combos", "th_cases",
           "pushover_cases", "staged_cases", "buckling_cases",
           "steady_state_cases", "psd_cases", "nonlinear_static_cases",
           "hyperstatic_cases", "cases_not_run", "analysis_options",
           "num_modes", "display_units", "modal_from_case")

_lock = threading.Lock()
_store: "OrderedDict[str, bytes]" = OrderedDict()
stats = {"hits": 0, "misses": 0}


def enabled() -> bool:
    return (os.environ.get("SKYFRAME_SLOW_PATH") != "1"
            and os.environ.get("SKYFRAME_MESH_CACHE", "1") != "0")


def _mesher_constants():
    """Module-level tuning constants of the meshers (UPPER_CASE scalars of
    core.mesh / framemesh / polymesh) — part of the key, so a changed
    constant (e.g. FOUNDATION_MIN_SEGMENTS) never serves a stale mesh."""
    from skyframe.core import framemesh, mesh, polymesh
    out = []
    for mod in (mesh, framemesh, polymesh):
        for k in sorted(vars(mod)):
            v = vars(mod)[k]
            if k.isupper() and isinstance(v, (int, float, str, bool, tuple)):
                out.append((mod.__name__, k, v))
    return out


def mesh_key(model) -> Optional[str]:
    """Geometry key of a model, or None when it cannot be computed."""
    try:
        shallow = copy.copy(model)
        for a in EXCLUDE:
            if a in getattr(shallow, "__dict__", {}):
                setattr(shallow, a, None)
        h = hashlib.sha256(pickle.dumps(shallow, protocol=4))
        h.update(repr(_mesher_constants()).encode())
        return h.hexdigest()
    except Exception:                                    # pragma: no cover
        return None


def cached_mesh(model) -> MeshedModel:
    """``mesh_model(model)``, served from the cache when the geometry key
    matches a recent run."""
    if not enabled():
        return mesh_model(model)
    key = mesh_key(model)
    if key is None:
        return mesh_model(model)
    with _lock:
        blob = _store.get(key)
        if blob is not None:
            _store.move_to_end(key)
            stats["hits"] += 1
    if blob is not None:
        mesh = pickle.loads(blob)
        mesh.model = model                  # the live model, not a copy
        return mesh
    mesh = mesh_model(model)
    owner = mesh.model
    try:
        mesh.model = None                   # geometry only in the blob
        blob = pickle.dumps(mesh, protocol=4)
    except Exception:                                    # pragma: no cover
        return mesh
    finally:
        mesh.model = owner
    with _lock:
        stats["misses"] += 1
        _store[key] = blob
        _store.move_to_end(key)
        while len(_store) > CAPACITY:
            _store.popitem(last=False)
    return mesh


def clear() -> None:
    with _lock:
        _store.clear()
        stats["hits"] = stats["misses"] = 0
