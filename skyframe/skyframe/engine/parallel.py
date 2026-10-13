"""Process-parallel independent solves on forked copies of the live domain.

CONTRACT "Meshed-model performance: parallel solves and fewer rebuilds".

On meshed buildings each linear solve costs about half a second, and
almost all of it is OpenSees' per-element loop (form the unbalance,
update and commit 8 000+ shells), not the factorization.  The solves of
one analysis family are independent of each other: every one runs on the
SAME reused elastic domain (``ops.reset()`` back to the initial state) with
the SAME factor-once UmfPack factorization, so its result does not depend
on which solves ran before it (the v0.26 reuse and factor-once invariants,
tested bit-identical).

This module runs such a family on several processes.  The parent finishes
the first item itself when the domain is not yet built and factored (a
"warm" family starts at once), then ``os.fork()`` gives each worker a copy-on-write image of the whole
interpreter: the live OpenSees domain, the factored system and the engine.
Each worker solves its share of the items and sends the Python results
back pickled (pickle round-trips floats exactly), then leaves with
``os._exit``.  The parent solves its own share meanwhile and merges by item
index, so the merged results are byte-identical to the serial loop, item
for item, whatever the timing.

Deterministic plan: with ``L`` lanes and ``n`` remaining items, item ``k``
goes to lane ``(n - 1 - k) % L``; lane 0 is the parent and does its items
in ascending order, so the parent always solves the LAST item last and the
interpreter is left exactly where the serial loop leaves it.

Anything unusual degrades to the serial loop, never to different results:
a worker that fails, crashes or cannot be forked has its items re-solved by
the parent (an analysis error then surfaces exactly as in the serial run).

Families: the plain linear static cases (:func:`prefetch_static`), the
condensed-modal unit-load columns and mode-shape solves
(``modal_condensed.solve`` via :func:`map_family`) and the solve-free RS
cases (:func:`prefetch_rs`).  :func:`reuse_for_modal` removes the domain
rebuilds between those families.

Lanes (``analysis_options.parallel``):

* ``"auto"`` (default): ``min(cores, MAX_LANES)`` lanes when the process may
  use at least :data:`MIN_CORES` cores, else serial;
* ``"off"``: always serial;
* an int ``1..MAX_LANES``: that many lanes (1 = serial).

Only the sparse (factor-once) solver policy goes parallel, i.e. models at
or above ``solver_policy.SPARSE_EQ_THRESHOLD`` equations; smaller models
always run the serial legacy path.  ``SKYFRAME_SLOW_PATH=1`` and
``SKYFRAME_PARALLEL=0`` run serial.

E1 (Windows-capable parallel solves): where ``fork`` is unavailable
(Windows, macOS) — or when ``analysis_options.parallel_start`` /
``SKYFRAME_PARALLEL_START`` asks for it — the static, condensation-column
and mode-shape families run on the persistent SPAWN worker pool of
:mod:`skyframe.engine.spawn_pool` instead (each worker rebuilds the domain
once per model, a probe item verifies it; results byte-identical).  Spawn
is used only at or above :data:`SPAWN_EQ_THRESHOLD` estimated equations
(the measured break-even, docs/PERFORMANCE.md "Windows-capable parallel
solves"); the solve-free RS family stays serial in spawn mode.  "auto"
lanes = ``min(physical performance cores, MAX_LANES)``
(:func:`performance_cores`; 8 on an 8P+16E Core Ultra 9 285K).
"""

from __future__ import annotations

import os
import pickle
import signal
import sys
import time
import warnings
from typing import Callable, Dict, List, Sequence

MIN_CORES = 4
MAX_LANES = 8
# E1: spawn workers pay a rebuild (process start + import + domain build
# from the pickled model and mesh); below this many estimated equations a
# solve is too cheap to win it back -> serial (measured, docs/PERFORMANCE.md
# "Windows-capable parallel solves")
SPAWN_EQ_THRESHOLD = 3000
START_METHODS = ("auto", "fork", "spawn")

stats = {"forks": 0, "worker_items": 0, "fallback_items": 0}
_IN_WORKER = {"flag": False}       # a worker never forks again


class _AnySparse:                   # lanes() pre-check before any build
    linear_system = "UmfPack"


_ANY_SPARSE = _AnySparse()


def option(model) -> object:
    """The model's ``analysis_options.parallel`` value ("auto" default)."""
    opts = getattr(model, "analysis_options", None) or {}
    if not isinstance(opts, dict):
        return "auto"
    return opts.get("parallel", "auto")


def cores() -> int:
    """Physical PERFORMANCE cores this process may run on (E1; see
    :func:`performance_cores`) — the "auto" lane count before the cap."""
    from skyframe.engine import cpu_topology as _cpu
    return _cpu.performance_cores()


def fork_supported() -> bool:
    return hasattr(os, "fork") and sys.platform.startswith("linux")


def start_method(model=None) -> str:
    """"fork" or "spawn" for this model's parallel families (E1).

    ``SKYFRAME_PARALLEL_START`` (env) overrides
    ``analysis_options.parallel_start``; "auto" (default) = fork where it
    exists (Linux), else spawn.  "fork" without fork support = spawn."""
    want = os.environ.get("SKYFRAME_PARALLEL_START") or "auto"
    if want not in START_METHODS:
        want = "auto"
    if want == "auto":
        opts = getattr(model, "analysis_options", None) or {}
        if isinstance(opts, dict):
            want = opts.get("parallel_start", "auto")
        if want not in START_METHODS:
            want = "auto"
    if want == "spawn" or not fork_supported():
        return "spawn"
    return "fork"


def lanes(model, asm=None) -> int:
    """Number of processes for an independent-solve family (1 = serial)."""
    from skyframe.engine import solver_policy as _slv
    opt = option(model)
    if (opt == "off" or _IN_WORKER["flag"]
            or os.environ.get("SKYFRAME_PARALLEL") == "0"
            or os.environ.get("SKYFRAME_SLOW_PATH") == "1"):
        return 1
    if asm is None or not _slv.factor_once_ok(asm):
        return 1
    if isinstance(opt, bool):
        return 1
    if (start_method(model) == "spawn"
            and hasattr(asm, "node_coords")
            and _slv.estimate_equations(asm) < SPAWN_EQ_THRESHOLD):
        return 1              # E1: too small to win back a worker rebuild
    if isinstance(opt, int):
        return max(1, min(int(opt), MAX_LANES))
    n = cores()
    return min(n, MAX_LANES) if n >= MIN_CORES else 1


def _spawn(model) -> bool:
    return start_method(model) == "spawn"


def plan(n: int, n_lanes: int) -> Dict[int, List[int]]:
    """Deterministic item -> lane assignment (lane 0 = the parent, which
    gets the last item)."""
    n_lanes = max(1, min(n_lanes, n))
    out: Dict[int, List[int]] = {lane: [] for lane in range(n_lanes)}
    for k in range(n):
        out[(n - 1 - k) % n_lanes].append(k)
    return out


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        k = os.write(fd, view)
        view = view[k:]


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        b = os.read(fd, 1 << 20)
        if not b:
            break
        chunks.append(b)
    return b"".join(chunks)


def fork_map(fn: Callable, items: Sequence, n_lanes: int) -> Dict[int, object]:
    """``{k: fn(items[k])}`` for every item, computed on ``n_lanes``
    processes (see the module docstring).  Items a worker could not deliver
    are computed by the parent afterwards, in item order."""
    n = len(items)
    if n == 0:
        return {}
    if n_lanes <= 1 or n == 1 or not fork_supported():
        # no fork (Windows, macOS): serial in the parent.  Callers that can
        # use the spawn pool route there first; this is the safe floor for
        # every other path (e.g. PARDISO-owned families, RS prefetch).
        return {k: fn(items[k]) for k in range(n)}
    lanes_of = plan(n, n_lanes)
    workers = []                                  # (pid, read fd, items)
    serial_extra: List[int] = []
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        for lane in range(1, len(lanes_of)):
            mine = lanes_of[lane]
            try:
                r, w = os.pipe()
            except OSError:                              # pragma: no cover
                serial_extra.extend(mine)
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", DeprecationWarning)
                    pid = os.fork()
            except OSError:                              # pragma: no cover
                os.close(r)
                os.close(w)
                serial_extra.extend(mine)
                continue
            if pid == 0:                                 # ---- worker
                code = 0
                try:
                    os.close(r)
                    _IN_WORKER["flag"] = True
                    signal.signal(signal.SIGINT, signal.SIG_DFL)
                    out = [(k, fn(items[k])) for k in mine]
                    payload = pickle.dumps(("ok", out),
                                           protocol=pickle.HIGHEST_PROTOCOL)
                except BaseException as exc:             # noqa: BLE001
                    payload = pickle.dumps(("err", repr(exc)))
                try:
                    _write_all(w, payload)
                    os.close(w)
                except BaseException:                    # noqa: BLE001
                    code = 1
                os._exit(code)
            os.close(w)
            stats["forks"] += 1
            workers.append((pid, r, mine))
        results: Dict[int, object] = {}
        for k in lanes_of[0]:
            results[k] = fn(items[k])
        for pid, r, mine in workers:
            try:
                data = _read_all(r)
            finally:
                os.close(r)
            os.waitpid(pid, 0)
            delivered = False
            try:
                tag, out = pickle.loads(data)
                if tag == "ok" and len(out) == len(mine):
                    for k, v in out:
                        results[k] = v
                    stats["worker_items"] += len(out)
                    delivered = True
            except Exception:                            # noqa: BLE001
                pass
            if not delivered:
                serial_extra.extend(mine)
        workers = []
    finally:
        for pid, r, _ in workers:                        # parent raised
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:                              # pragma: no cover
                pass
            try:
                os.close(r)
            except OSError:                              # pragma: no cover
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:                              # pragma: no cover
                pass
    for k in sorted(serial_extra):
        stats["fallback_items"] += 1
        results[k] = fn(items[k])
    return results


def _pardiso_owned(eng) -> bool:
    """E2: a spawned worker has no PARDISO factor, and the parent solves
    each further item with a cheap back-substitution, so PARDISO-owned
    families stay in the parent (CONTRACT "Windows-capable parallel solves")."""
    try:
        from skyframe.engine import pardiso_solver as _pds
        return bool(_pds.active(eng))
    except Exception:
        return False


def map_family(fn: Callable, items: Sequence, n_lanes: int,
               warm: bool = False, spawn=None) -> List[object]:
    """``[fn(x) for x in items]``: the first item serially in the parent
    (builds / factors the domain the workers inherit) unless ``warm`` (the
    factored analysis is already live), the rest through :func:`fork_map`;
    results in item order.

    ``spawn`` = ``(engine, kind, common)``: in spawn mode (E1) the rest
    goes to the spawn pool instead; the parent always solves the first
    item itself (the workers' probe) — ``warm`` is irrelevant there."""
    if not items:
        return []
    if (spawn is not None and n_lanes > 1 and len(items) > 1
            and _spawn(spawn[0].model) and not _pardiso_owned(spawn[0])):
        from skyframe.engine import spawn_pool as _sp
        eng, kind, common = spawn
        first = fn(items[0])
        rest = _sp.pool_map(eng, kind, common, items[1:], n_lanes, fn,
                            probe_item=items[0], probe_result=first)
        return [first] + [rest[k] for k in range(len(items) - 1)]
    if warm and n_lanes > 1:
        out = fork_map(fn, items, n_lanes)
        return [out[k] for k in range(len(items))]
    first = fn(items[0])
    rest = fork_map(fn, items[1:], n_lanes)
    return [first] + [rest[k] for k in range(len(items) - 1)]


# ------------------------------------------------------- static prefetch
def prefetch_static(eng, names: Sequence[str]) -> None:
    """Solve the plain linear static cases among ``names`` in parallel and
    store them in the engine's case cache (``run()``'s loop then reads the
    cache).  No-op unless at least two such cases exist and the elastic
    build uses the factor-once sparse system with more than one lane."""
    model = eng.model
    elig = [n for n in names
            if n in model.cases and n not in eng._case_cache
            and model.cases[n].effective_geometric == "linear"]
    # the first case must be the loop's first anyway (run order unchanged)
    if (len(elig) < 2 or elig[0] != names[0]
            or lanes(model, _ANY_SPARSE) <= 1
            or eng._axial_only_present()
            or eng._nonlinear_static_links_present()
            or eng._compression_only_springs_present()):
        return
    from skyframe.engine.opensees_engine import _REUSE, _reuse_owner
    times = eng.__dict__.setdefault("_par_case_times", {})
    t0 = time.perf_counter()
    try:
        res0 = eng.run_static(elig[0])     # build + factor in the parent
    except (RuntimeError, ValueError, ArithmeticError):
        _REUSE["owner"] = None             # as run()'s attempt() does;
        _REUSE["loaded"] = False           # the loop re-runs and reports
        _REUSE["factored"] = None
        return
    times[elig[0]] = time.perf_counter() - t0
    n_lanes = lanes(model, eng._asm)
    if n_lanes <= 1 or _reuse_owner() is not eng:
        return

    def solve(name):
        return static_item(eng, name)

    if _spawn(model):                      # E1: spawn worker pool
        from skyframe.engine import spawn_pool as _sp
        out = _sp.pool_map(eng, "static", {}, elig[1:], n_lanes, solve,
                           probe_item=elig[0],
                           probe_result=res0)
    else:
        out = fork_map(solve, elig[1:], n_lanes)
    for k, name in enumerate(elig[1:]):
        rec = out.get(k)
        if rec is None:
            continue
        res, dt = rec
        eng._case_cache[name] = res
        times[name] = dt


def static_item(eng, name):
    """``(CaseResults, wall time)`` of one plain linear static case, or
    None when it fails (the run() loop then re-runs and reports it, as
    ``attempt()`` does).  Shared by the forked and the spawned workers."""
    from skyframe.engine.opensees_engine import _REUSE
    t = time.perf_counter()
    try:
        res = eng.run_static(name)
    except (RuntimeError, ValueError, ArithmeticError):
        _REUSE["owner"] = None
        _REUSE["loaded"] = False
        _REUSE["factored"] = None
        return None
    return res, time.perf_counter() - t


def factored_live(eng, asm) -> bool:
    """True when ``asm`` is this engine's reused elastic domain and its
    factor-once analysis is live (workers can solve right away)."""
    from skyframe.engine.opensees_engine import _REUSE, _reuse_owner
    ref = _REUSE.get("factored")
    return (ref is not None and ref() is asm and _reuse_owner() is eng
            and eng._asm is asm)


def prefetch_rs(eng, names: Sequence[str]) -> None:
    """Evaluate response-spectrum cases in parallel into the RS cache.

    Only for the solve-free RS path of large models: the modal result each
    case asks for is already cached and carries the condensed path's unit
    modal responses (so a case is pure arithmetic on them: scaling + CQC /
    SRSS), and the case uses the legacy CQC / SRSS combination.  Each
    worker returns its finished case; ``run()``'s loop then reads the
    cache.  Byte-identical to the serial loop (same arithmetic, pickled
    floats)."""
    from skyframe.engine import modalcombo as _mc
    from skyframe.engine import modal_condensed as _mcd
    from skyframe.core import rs_ext as _crx
    model = eng.model
    asm = eng._asm
    n_lanes = lanes(model, asm)
    todo = [n for n in names if n in model.rs_cases
            and n not in eng._rs_cache]
    if n_lanes <= 1 or len(todo) < 2 or _spawn(model):
        return          # (E1: spawn mode keeps the cheap RS family serial)
    n_massed = asm.free_massed_dofs()
    for name in todo:
        rs = model.rs_cases[name]
        if not _mc.is_legacy(rs) or _crx.is_extended(rs) \
                or _crx.has_ecc(rs):           # RS ext: solves -> serial
            return
        n = min((rs.num_modes if rs.num_modes > 0 else None)
                or model.num_modes, n_massed)
        modal = eng._modal_cache.get(n)
        if (n <= 0 or modal is None or not modal.periods
                or _mcd.unit_responses(eng, modal) is None):
            return

    def evaluate(name):
        t = time.perf_counter()
        try:
            res = eng.run_response_spectrum(name)
        except (RuntimeError, ValueError, ArithmeticError):
            return None                    # the run() loop reports it
        return res, time.perf_counter() - t

    times = eng.__dict__.setdefault("_par_case_times", {})
    out = fork_map(evaluate, todo, n_lanes)
    for k, name in enumerate(todo):
        if out.get(k) is not None:
            eng._rs_cache[name], times[name] = out[k]


def prefetched_time(eng, name: str) -> float:
    """Wall time a prefetched case took (0.0 when not prefetched)."""
    return float(eng.__dict__.get("_par_case_times", {}).pop(name, 0.0))


# ------------------------------------------------- fewer domain rebuilds
def reuse_for_modal(eng, num_modes):
    """``(cached ModalResults | None, reusable assembly | None)`` for
    :meth:`OpenSeesEngine.run_modal` on large models.

    ``run_modal`` used to start with a full ``_build()`` — also for the RS
    cases' calls that then hit the modal cache — and the next reused-domain
    solve (CR) had to rebuild once more.  When the live domain is THIS
    engine's reused standard elastic build (exactly what ``_build()`` would
    make: the v0.26 reuse invariant), the sparse factor-once policy is
    active and the condensed modal path applies, a cache hit needs no build
    and a miss runs on the reset live domain.  ``(None, None)`` = the
    unchanged legacy path (small models, ``eigen_solver: "legacy"``,
    ``SKYFRAME_SLOW_PATH=1``, no live domain).
    """
    from skyframe.engine import modal_condensed as _mcd
    from skyframe.engine import solver_policy as _slv
    from skyframe.engine.opensees_engine import _reuse_owner, _slow_path
    asm = eng._asm
    model = eng.model
    if (_slow_path() or asm is None or _reuse_owner() is not eng
            or not _slv.factor_once_ok(asm) or not _mcd.enabled(model)):
        return None, None
    n = min(num_modes or model.num_modes, asm.free_massed_dofs())
    if n <= 0:
        return None, None
    hit = eng._modal_cache.get(n)
    if hit is not None:
        return hit, None
    if not _mcd.applicable(eng, asm, n):
        return None, None
    return None, eng._elastic_domain()
