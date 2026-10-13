"""Spawn-safe persistent worker pool for the parallel solve families.

CONTRACT "Windows-capable parallel solves (spawn worker pool)".

:mod:`skyframe.engine.parallel` runs the independent solves of one family
(linear static cases, condensation columns, mode-shape solves) on
``os.fork()`` copies of the live OpenSees domain.  Windows has no fork, so
this module provides the same families on a pool of SPAWNED processes:

* ``N`` worker processes (``multiprocessing`` "spawn" context: a fresh
  interpreter, nothing inherited) are started on first use and kept warm.
  ``OMP_NUM_THREADS`` / ``OPENBLAS_NUM_THREADS`` / ``MKL_NUM_THREADS`` are
  set to 1 in the environment the child starts with, i.e. before numpy or
  OpenSees are imported there.
* Each worker rebuilds the OpenSees domain ONCE per model from the pickled
  ``engine.source_model`` (pickle round-trips every float exactly) and the
  parent's mesh (so it does not re-mesh), then serves batches of items.
  The worker keeps its engine and domain between batches AND between runs:
  a later run of an unchanged model reuses it.  The pool is keyed on a
  SHA-256 of the model's canonical JSON (``to_dict``); any model edit
  changes the key and the workers rebuild from the new model.
* Items go to lanes by the same deterministic plan as the fork path
  (``parallel.plan``); lane 0 is the parent, which solves the last item
  last, and results merge by item index.
* Verification ("probe"): each batch carries the family's first item,
  which the parent solved itself.  A worker solves it too, on its own
  domain, and returns the SHA-256 of the pickled result; the parent only
  accepts the worker's items when that digest equals the digest of its
  own result.  Anything that makes the rebuilt domain differ from the
  parent's (engine state set after construction, a non-deterministic
  build) is therefore caught and degrades to the serial loop.
* A worker that raises, dies, or fails the probe has its items re-solved
  by the parent (results identical to the serial run).

The worker never imports the GUI or the HTTP server: its target is
:func:`_worker_main` in this module, which imports only the engine.
Frozen builds (PyInstaller) must call ``multiprocessing.freeze_support()``
first thing in the entry point (``packaging/launch.py``,
``skyframe.desktop.main``).
"""

from __future__ import annotations

import atexit
import collections
import hashlib
import json
import os
import pickle
import threading
import time
from typing import Callable, Dict, List, Optional, Sequence

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
               "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
WORKER_ENV = "SKYFRAME_SPAWN_WORKER"
IDLE_SHUTDOWN_S = float(os.environ.get("SKYFRAME_POOL_IDLE_S", "900"))

stats = {"starts": 0, "inits": 0, "batches": 0, "worker_items": 0,
         "fallback_items": 0, "probe_mismatch": 0, "deaths": 0}

_PROTO = pickle.HIGHEST_PROTOCOL
# diagnostics: per-batch timings of the latest families (bounded)
trace: "collections.deque" = collections.deque(maxlen=200)


def digest(obj) -> str:
    """SHA-256 of the pickled object (the probe comparison)."""
    return hashlib.sha256(pickle.dumps(obj, protocol=_PROTO)).hexdigest()


def model_key(model) -> str:
    """Canonical model hash: SHA-256 of the sorted-key JSON of
    ``model.to_dict()`` (the save format, so an equal model loaded again
    gives the same key; any edit changes it)."""
    try:
        txt = json.dumps(model.to_dict(), sort_keys=True, default=str)
    except Exception:                                    # pragma: no cover
        return "unkeyed-%d" % id(model)
    return hashlib.sha256(txt.encode()).hexdigest()


def engine_key(eng) -> str:
    """Model key of an engine (memoized: the model is frozen once the
    engine is constructed)."""
    k = eng.__dict__.get("_spawn_key")
    if k is None:
        k = eng.__dict__["_spawn_key"] = model_key(eng.source_model)
    return k


# =========================================================================
# worker side
# =========================================================================
_W: Dict[str, object] = {"key": None, "eng": None, "err": None,
                         "fault": None}


def _w_init(key, model_blob, mesh_blob) -> None:
    import openseespy.opensees as ops
    from skyframe.engine import opensees_engine as _oe
    _W["eng"] = None
    _W["key"] = key
    _W["err"] = None
    ops.wipe()
    _oe._REUSE["owner"] = None
    _oe._REUSE["loaded"] = False
    _oe._REUSE["factored"] = None
    try:
        model = pickle.loads(model_blob)
        eng = _oe.OpenSeesEngine(model)
        if mesh_blob is not None:
            mesh = pickle.loads(mesh_blob)
            mesh.model = eng.model          # as core.mesh_cache does
            eng._mesh = mesh
        eng._elastic_domain()               # the one build, eagerly
        _W["eng"] = eng
    except BaseException as exc:            # noqa: BLE001
        _W["err"] = repr(exc)


def _w_handler(eng, kind: str, common: dict) -> Callable:
    """The per-item function of a family, on the worker's own domain —
    the very functions the parent's serial loop calls."""
    if kind == "echo":                      # tests: pure arithmetic
        return lambda x: x * x / 3.0
    if kind == "static":
        from skyframe.engine import parallel as _par
        return lambda name: _par.static_item(eng, name)
    from skyframe.engine import modal_condensed as _mcd
    asm = eng._elastic_domain()
    if kind == "mcd_column":
        masters = set(asm.masters.values())
        dofs = [tuple(k) for k in common["dofs"]]
        return lambda key: _mcd.g_column_item(eng, dofs, masters,
                                              tuple(key))
    if kind == "mcd_mode":
        tags = list(asm.node_coords)
        return lambda job: _mcd.mode_item(eng, asm, tags,
                                          common["want_units"], job)
    raise ValueError(f"unknown family kind {kind!r}")


def _probe_view(kind: str, out):
    """The part of an item result the probe compares (a static item's
    wall time is not part of the result)."""
    if kind == "static":
        return None if out is None else out[0]
    return out


def _w_run(kind, common, probe_item, items):
    fault = _W.pop("fault", None)
    _W["fault"] = None
    if fault == "exit":
        os._exit(3)
    if fault == "raise":
        raise RuntimeError("injected worker fault (test)")
    if kind == "echo":
        eng = None
    else:
        if _W["eng"] is None:
            raise RuntimeError(f"worker has no domain: {_W['err']}")
        eng = _W["eng"]
    fn = _w_handler(eng, kind, common)
    pd = None
    t0 = time.perf_counter()
    if probe_item is not None:
        pd = digest(_probe_view(kind, fn(probe_item)))
        if fault == "mismatch":
            pd = "0" * 64
    t1 = time.perf_counter()
    out = [(k, fn(x)) for k, x in items]
    _W["timing"] = {"init_s": _W.pop("init_s", 0.0), "probe_s": t1 - t0,
                    "items_s": time.perf_counter() - t1}
    return pd, out


def _worker_main(conn, env: dict) -> None:
    """Entry point of a spawned worker: serve messages until "stop"."""
    os.environ.update(env)
    from skyframe.engine import parallel as _par
    _par._IN_WORKER["flag"] = True          # a worker never fans out
    try:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)   # parent handles ^C
    except Exception:                                  # pragma: no cover
        pass
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            break
        op = msg[0]
        if op == "stop":
            break
        if op == "init":
            t = time.perf_counter()
            _w_init(*msg[1:])
            _W["init_s"] = time.perf_counter() - t
            continue
        if op == "fault":
            _W["fault"] = msg[1]
            continue
        if op == "run":
            try:
                pd, out = _w_run(*msg[1:])
                reply = ("ok", pd, out, _W.get("timing"))
            except BaseException as exc:                # noqa: BLE001
                reply = ("err", repr(exc), None, None)
            try:
                conn.send(reply)
            except Exception:                           # noqa: BLE001
                try:
                    conn.send(("err", "unpicklable result", None, None))
                except Exception:                       # pragma: no cover
                    break
    try:
        conn.close()
    except Exception:                                   # pragma: no cover
        pass


# =========================================================================
# parent side
# =========================================================================
class _Worker:
    def __init__(self, proc, conn):
        self.proc = proc
        self.conn = conn
        self.key: Optional[str] = None
        self.bad_key: Optional[str] = None      # failed a probe for key

    def alive(self) -> bool:
        return self.proc.is_alive()


class _Pool:
    def __init__(self):
        self.workers: List[_Worker] = []
        self.lock = threading.Lock()
        self.timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------ process
    def _start_one(self) -> _Worker:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        parent_conn, child_conn = ctx.Pipe(duplex=True)
        env = {v: "1" for v in THREAD_VARS}
        env[WORKER_ENV] = "1"
        env["SKYFRAME_PARALLEL"] = "0"          # belt and braces
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)                  # the child starts with it
        try:
            proc = ctx.Process(target=_worker_main, args=(child_conn, env),
                               name="skyframe-solver", daemon=True)
            proc.start()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        child_conn.close()
        stats["starts"] += 1
        return _Worker(proc, parent_conn)

    def ensure(self, n: int) -> List[_Worker]:
        self.workers = [w for w in self.workers if w.alive()]
        while len(self.workers) < n:
            self.workers.append(self._start_one())
        return self.workers[:n]

    def drop(self, w: _Worker) -> None:
        stats["deaths"] += 1
        try:
            w.proc.kill()
        except Exception:                                # pragma: no cover
            pass
        try:
            w.proc.join(5)
            w.conn.close()
        except Exception:                                # pragma: no cover
            pass
        if w in self.workers:
            self.workers.remove(w)

    def shutdown(self) -> None:
        for w in list(self.workers):
            try:
                w.conn.send(("stop",))
            except Exception:                            # noqa: BLE001
                pass
        for w in list(self.workers):
            try:
                w.proc.join(2)
                if w.proc.is_alive():
                    w.proc.kill()
                    w.proc.join(2)
                w.conn.close()
            except Exception:                            # pragma: no cover
                pass
        self.workers = []

    def touch(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
        if IDLE_SHUTDOWN_S > 0:
            self.timer = threading.Timer(IDLE_SHUTDOWN_S, self._idle)
            self.timer.daemon = True
            self.timer.start()

    def _idle(self) -> None:
        if self.lock.acquire(blocking=False):
            try:
                self.shutdown()
            finally:
                self.lock.release()


_POOL = _Pool()


def shutdown() -> None:
    """Stop every pooled worker (atexit; tests)."""
    with _POOL.lock:
        if _POOL.timer is not None:
            _POOL.timer.cancel()
            _POOL.timer = None
        _POOL.shutdown()


atexit.register(shutdown)


def workers_info() -> List[dict]:
    """Diagnostics: one ``{"pid", "alive", "key"}`` per pooled worker."""
    return [{"pid": w.proc.pid, "alive": w.alive(), "key": w.key}
            for w in _POOL.workers]


def inject_fault(lane: int, mode: str) -> None:
    """Tests: make worker ``lane`` (1-based) fail its next batch
    (``"raise"``, ``"exit"`` = dies, ``"mismatch"`` = wrong probe)."""
    w = _POOL.ensure(lane)[lane - 1]
    w.conn.send(("fault", mode))


def _mesh_blob(eng) -> Optional[bytes]:
    """The parent's mesh of ``eng.model`` as the mesh cache stores it
    (geometry only), so workers skip re-meshing; None = workers mesh."""
    try:
        from skyframe.core import mesh_cache as _mc
        k = _mc.mesh_key(eng.model)
        blob = _mc._store.get(k) if k is not None else None
        if blob is not None:
            return blob
        mesh = getattr(eng, "_mesh", None)
        if mesh is None:
            return None
        owner = mesh.model
        mesh.model = None
        try:
            return pickle.dumps(mesh, protocol=4)
        finally:
            mesh.model = owner
    except Exception:                                    # pragma: no cover
        return None


def _init_workers(eng, ws: List[_Worker]) -> List[_Worker]:
    key = engine_key(eng)
    need = [w for w in ws if w.key != key]
    if need:
        model_blob = pickle.dumps(eng.source_model, protocol=_PROTO)
        mesh = _mesh_blob(eng)
        for w in need:
            w.conn.send(("init", key, model_blob, mesh))
            w.key = key
            w.bad_key = None
            stats["inits"] += 1
    return [w for w in ws if w.bad_key != key]


def pool_map(eng, kind: str, common: dict, items: Sequence,
             n_lanes: int, fn: Callable, probe_item=None,
             probe_result=None) -> Dict[int, object]:
    """``{k: fn(items[k])}`` with lanes ``1..n_lanes-1`` on pooled spawn
    workers (the parent is lane 0).  ``probe_item`` / ``probe_result``:
    an item the parent already solved (the family's first) and the
    comparable part of its result (see the module docstring)."""
    from skyframe.engine.parallel import plan
    n = len(items)
    if n == 0:
        return {}
    if n_lanes <= 1 or n == 1 or not _POOL.lock.acquire(blocking=False):
        return {k: fn(items[k]) for k in range(n)}
    results: Dict[int, object] = {}
    serial_extra: List[int] = []
    busy: list = []                                  # (worker, its items)
    try:
        lanes_of = plan(n, n_lanes)
        try:
            ws = _POOL.ensure(len(lanes_of) - 1)
            ws_ok = _init_workers(eng, ws)
        except Exception:                                # noqa: BLE001
            ws, ws_ok = [], []
        for lane in range(1, len(lanes_of)):
            mine = lanes_of[lane]
            w = ws[lane - 1] if lane - 1 < len(ws) else None
            if w is None or w not in ws_ok:
                serial_extra.extend(mine)
                continue
            try:
                w.conn.send(("run", kind, common, probe_item,
                             [(k, items[k]) for k in mine]))
                busy.append((w, mine))
            except Exception:                            # noqa: BLE001
                _POOL.drop(w)
                serial_extra.extend(mine)
        stats["batches"] += len(busy)
        t_own = time.perf_counter()
        for k in lanes_of[0]:
            results[k] = fn(items[k])
        trace.append({"kind": kind, "lane": 0, "items": len(lanes_of[0]),
                      "parent_own_s": round(time.perf_counter() - t_own,
                                            3)})
        want = (digest(probe_result) if probe_item is not None else None)
        while busy:
            w, mine = busy.pop(0)
            try:
                t_wait = time.perf_counter()
                tag, pd, out, timing = w.conn.recv()
                trace.append({"kind": kind, "items": len(mine),
                              "parent_wait_s": round(time.perf_counter()
                                                     - t_wait, 3),
                              **{k: round(v, 3) for k, v in
                                 (timing or {}).items()}})
            except Exception:                            # noqa: BLE001
                _POOL.drop(w)                            # died
                serial_extra.extend(mine)
                continue
            if tag != "ok" or out is None or len(out) != len(mine):
                serial_extra.extend(mine)
                continue
            if probe_item is not None and pd != want:
                stats["probe_mismatch"] += 1
                w.bad_key = w.key                        # not for this model
                serial_extra.extend(mine)
                continue
            for k, v in out:
                results[k] = v
            stats["worker_items"] += len(out)
    finally:
        for w, _ in busy:           # the parent raised: replies unread ->
            _POOL.drop(w)           # these workers cannot be reused
        _POOL.touch()
        _POOL.lock.release()
    for k in sorted(serial_extra):
        stats["fallback_items"] += 1
        results[k] = fn(items[k])
    return results


def in_worker() -> bool:
    """True inside a spawned solver worker."""
    return os.environ.get(WORKER_ENV) == "1"

