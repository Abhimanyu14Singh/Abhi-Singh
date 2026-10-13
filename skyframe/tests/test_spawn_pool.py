"""Windows-capable parallel solves: the persistent SPAWN worker pool (E1).

CONTRACT "Windows-capable parallel solves (spawn worker pool)";
docs/PERFORMANCE.md "Windows-capable parallel solves" has the numbers.

Pinned here (on Linux, forcing the spawn start method — the code path
Windows takes):

* a spawn-pool run is BYTE-IDENTICAL (SHA-256 of the results JSON) to the
  serial run on the meshed slab + wall benchmark model and on the mixed-
  feature model, and every pooled worker passed its probe;
* the pool stays warm across runs of an unchanged model (no re-init) and
  any model edit re-initializes the workers (new model key) — the edited
  run still equals its own serial run;
* a worker that dies, raises, or fails its probe has its items re-solved
  by the parent (identical results);
* ``multiprocessing.freeze_support()`` comes first in the frozen entry
  point and in ``desktop.main``; ``--selftest-parallel`` exits 0 both
  through ``python -m skyframe.desktop`` (the child re-imports the main
  module and must not start the server) and with the GLOBAL start method
  set to "spawn";
* lane policy: spawn threshold, start-method resolution, performance-core
  detection from a hybrid sysfs tree (closed form), option validation and
  serialization (default models byte-identical), DB-table round trip.
"""

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ops = pytest.importorskip("openseespy.opensees")

from skyframe.core import modeltables as MT
from skyframe.core.builder import quick_building
from skyframe.engine import cpu_topology as cpu
from skyframe.engine import parallel as par
from skyframe.engine import spawn_pool as sp
from skyframe.engine.opensees_engine import OpenSeesEngine

sys.path.insert(0, os.path.dirname(__file__))
from test_meshed_perf import _meshed, _rich  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True, scope="module")
def _pool_lifecycle():
    yield
    sp.shutdown()


@pytest.fixture
def spawn_any_size(monkeypatch):
    """The test models are far below the measured spawn break-even."""
    monkeypatch.setattr(par, "SPAWN_EQ_THRESHOLD", 0)


def _sha(model, **opts):
    m = copy.deepcopy(model)
    m.analysis_options = opts
    d = OpenSeesEngine(m).run().to_dict()
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str)
                          .encode()).hexdigest()


def _delta(before):
    return {k: sp.stats[k] - before[k] for k in sp.stats}


# =========================================================================
# 1. spawn == serial, byte for byte
# =========================================================================
def test_spawn_run_is_byte_identical_to_serial(spawn_any_size):
    m = _meshed()
    ser = _sha(m, parallel="off")
    s0 = dict(sp.stats)
    out = _sha(m, parallel=4, parallel_start="spawn")
    d = _delta(s0)
    assert out == ser
    assert d["worker_items"] > 0 and d["probe_mismatch"] == 0
    assert d["fallback_items"] == 0
    # static (4 of 5 cases), columns, mode shapes all went to the pool
    assert d["batches"] >= 3 * 2


def test_spawn_identical_with_mixed_features(spawn_any_size):
    m = _rich()
    ser = _sha(m, parallel="off")
    s0 = dict(sp.stats)
    assert _sha(m, parallel=3, parallel_start="spawn") == ser
    d = _delta(s0)
    assert d["worker_items"] > 0 and d["probe_mismatch"] == 0


# =========================================================================
# 2. warm pool across runs; model edits invalidate it
# =========================================================================
def test_warm_pool_reused_and_invalidated_by_model_edit(spawn_any_size):
    m = _meshed()
    _sha(m, parallel=3, parallel_start="spawn")       # (re)initializes
    keys = {w["key"] for w in sp.workers_info()[:2]}   # lanes 1, 2
    assert len(keys) == 1
    s0 = dict(sp.stats)
    _sha(m, parallel=3, parallel_start="spawn")       # unchanged model
    d = _delta(s0)
    assert d["inits"] == 0 and d["starts"] == 0 and d["worker_items"] > 0
    edited = copy.deepcopy(m)
    edited.pattern("SDL", "dead").area_loads[0].q *= 1.5
    ser = _sha(edited, parallel="off")
    s0 = dict(sp.stats)
    assert _sha(edited, parallel=3, parallel_start="spawn") == ser
    d = _delta(s0)
    assert d["inits"] == 2 and d["starts"] == 0      # same processes
    assert {w["key"] for w in sp.workers_info()[:2]} != keys
    assert d["probe_mismatch"] == 0


def test_model_key_is_canonical():
    a = quick_building(stories=2)
    b = quick_building(stories=2)
    assert sp.model_key(a) == sp.model_key(b)
    b.stories[0].height += 0.1
    assert sp.model_key(a) != sp.model_key(b)


# =========================================================================
# 3. faults: the parent re-solves, results unchanged
# =========================================================================
@pytest.mark.parametrize("mode,counter", [("exit", "deaths"),
                                          ("raise", "fallback_items"),
                                          ("mismatch", "probe_mismatch")])
def test_worker_fault_falls_back_to_parent(spawn_any_size, mode, counter):
    m = _meshed()
    ser = _sha(m, parallel="off")
    _sha(m, parallel=3, parallel_start="spawn")       # warm the pool
    sp.inject_fault(1, mode)
    s0 = dict(sp.stats)
    assert _sha(m, parallel=3, parallel_start="spawn") == ser
    d = _delta(s0)
    assert d[counter] >= 1 and d["fallback_items"] >= 1
    sp.shutdown()                    # (a mismatched worker is benched)


# =========================================================================
# 4. lanes, start method, thresholds
# =========================================================================
class _Sparse:
    linear_system = "UmfPack"


def test_start_method_resolution(monkeypatch):
    m = quick_building()
    monkeypatch.delenv("SKYFRAME_PARALLEL_START", raising=False)
    auto = "fork" if par.fork_supported() else "spawn"
    assert par.start_method(m) == auto
    m.analysis_options = {"parallel_start": "spawn"}
    assert par.start_method(m) == "spawn"
    m.analysis_options = {"parallel_start": "fork"}
    assert par.start_method(m) == auto          # no fork -> spawn
    monkeypatch.setenv("SKYFRAME_PARALLEL_START", "spawn")
    assert par.start_method(m) == "spawn"       # env wins
    monkeypatch.setattr(par, "fork_supported", lambda: False)
    monkeypatch.delenv("SKYFRAME_PARALLEL_START")
    m.analysis_options = {}
    assert par.start_method(m) == "spawn"       # Windows


def test_spawn_threshold_keeps_small_models_serial(monkeypatch):
    m = _meshed()
    m.analysis_options = {"parallel": 4, "parallel_start": "spawn"}
    eng = OpenSeesEngine(m)
    asm = eng._elastic_domain()
    from skyframe.engine import solver_policy as slv
    neq = slv.estimate_equations(asm)
    assert slv.SPARSE_EQ_THRESHOLD <= neq < par.SPAWN_EQ_THRESHOLD
    assert par.lanes(m, asm) == 1               # spawn would not pay
    assert par.lanes(m, _Sparse()) == 4         # pre-build check passes
    monkeypatch.setattr(par, "SPAWN_EQ_THRESHOLD", neq)
    assert par.lanes(m, asm) == 4
    m.analysis_options = {"parallel": 4, "parallel_start": "fork"}
    if par.fork_supported():
        monkeypatch.setattr(par, "SPAWN_EQ_THRESHOLD", 10 ** 9)
        assert par.lanes(m, asm) == 4           # fork: no rebuild cost
    s0 = dict(sp.stats)
    m.analysis_options = {"parallel": 4, "parallel_start": "spawn"}
    monkeypatch.setattr(par, "SPAWN_EQ_THRESHOLD", 10 ** 9)
    eng = OpenSeesEngine(m)
    eng.run()
    assert _delta(s0)["batches"] == 0           # below threshold: serial


def _fake_sysfs(root: Path, n_logical: int, smt: int, pcore_list=None):
    for c in range(n_logical):
        t = root / "system" / "cpu" / f"cpu{c}" / "topology"
        t.mkdir(parents=True)
        (t / "core_id").write_text(f"{c // smt}\n")
        (t / "physical_package_id").write_text("0\n")
    if pcore_list is not None:
        (root / "cpu_core").mkdir()
        (root / "cpu_core" / "cpus").write_text(pcore_list + "\n")


def test_performance_cores_from_hybrid_sysfs(tmp_path, monkeypatch):
    # 285K-like: 8 P-cores (no SMT) cpus 0-7 + 16 E-cores cpus 8-23
    a = tmp_path / "a"
    _fake_sysfs(a, 24, 1, "0-7")
    assert cpu.linux_performance_cores(range(24), str(a)) == 8
    assert cpu.linux_performance_cores(range(4, 24), str(a)) == 4
    # hybrid with SMT P-cores: 6 P x 2 threads (0-11) + 8 E (12-19)
    b = tmp_path / "b"
    _fake_sysfs(b, 20, 2, "0-11")
    assert cpu.linux_performance_cores(range(20), str(b)) == 6
    # non-hybrid, SMT 2: 8 logical = 4 physical
    c = tmp_path / "c"
    _fake_sysfs(c, 8, 2)
    assert cpu.linux_performance_cores(range(8), str(c)) == 4
    assert cpu._parse_cpu_list("0-3,8,10-11") == [0, 1, 2, 3, 8, 10, 11]
    monkeypatch.setenv("SKYFRAME_PERF_CORES", "8")
    assert cpu.performance_cores() == 8
    m = quick_building()
    if par.fork_supported():
        assert par.lanes(m, _Sparse()) == 8     # min(P-cores, 8)


# =========================================================================
# 5. analysis_options.parallel_start: validation, serialization, table
# =========================================================================
def test_parallel_start_option_validation_and_serialization():
    m = quick_building()
    base = json.dumps(m.to_dict(), sort_keys=True)
    m.analysis_options = {"parallel_start": "auto"}
    m.validate()
    assert json.dumps(m.to_dict(), sort_keys=True) == base   # default
    m.analysis_options = {"parallel_start": "spawn"}
    m.validate()
    d = m.to_dict()
    assert d["analysis_options"] == {"parallel_start": "spawn"}
    from skyframe.core.model import BuildingModel
    assert BuildingModel.from_dict(d).analysis_options == \
        {"parallel_start": "spawn"}
    for bad in ("thread", "", 1, True, None):
        m.analysis_options = {"parallel_start": bad}
        with pytest.raises(ValueError, match="parallel_start"):
            m.validate()


def test_parallel_start_db_table_round_trip():
    d = quick_building().to_dict()
    rows = json.loads(json.dumps(MT.get_table(d, "analysis_options")
                                 ["rows"]))
    assert rows[0]["parallel_start"] == "auto"
    rows[0]["parallel_start"] = "spawn"
    m, errs, _ = MT.apply_tables(copy.deepcopy(d),
                                 {"analysis_options": rows})
    assert errs == [] and m.analysis_options == {"parallel_start": "spawn"}
    rows[0]["parallel_start"] = "auto"
    m, errs, _ = MT.apply_tables(m.to_dict(), {"analysis_options": rows})
    assert errs == [] and "analysis_options" not in m.to_dict()


# =========================================================================
# 6. frozen entry point: freeze_support guard, self-test flag
# =========================================================================
def _first_call_names(body):
    out = []
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func,
                                                        ast.Attribute):
                out.append(sub.func.attr)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                out.append("import:" + (getattr(sub, "module", None)
                                        or sub.names[0].name))
    return out


def test_freeze_support_guard_in_entry_points():
    tree = ast.parse((ROOT / "packaging" / "launch.py").read_text())
    guard = [n for n in tree.body if isinstance(n, ast.If)]
    assert len(guard) == 1
    seq = _first_call_names(guard[0].body)
    assert seq[0] == "freeze_support"
    assert seq.index("freeze_support") < seq.index("import:skyframe.desktop")
    # no top-level import of the app (a frozen worker must not load it
    # before freeze_support dispatches)
    top = [n for n in tree.body if isinstance(n, (ast.Import,
                                                  ast.ImportFrom))]
    assert all("skyframe" not in ast.dump(n) for n in top)
    dtree = ast.parse((ROOT / "skyframe" / "desktop.py").read_text())
    main = next(n for n in dtree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = _first_call_names(main.body)
    assert calls.index("freeze_support") < calls.index("ArgumentParser")
    spec = (ROOT / "packaging" / "skyframe.spec").read_text()
    assert '["launch.py"]' in spec              # the guarded entry


def _run_py(args, **kw):
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable] + args, cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=600, **kw)


def test_selftest_parallel_flag_via_desktop_main():
    r = _run_py(["-m", "skyframe.desktop", "--selftest-parallel"])
    assert r.returncode == 0, r.stdout + r.stderr
    line = [s for s in r.stdout.splitlines()
            if s.startswith("SkyFrame parallel self-test ")][-1]
    rep = json.loads(line.split("self-test ", 1)[1])
    assert rep["ok"] and rep["identical"] and rep["worker_items"] > 0
    assert "running at" not in r.stdout          # no server was started


def test_selftest_with_global_spawn_start_method(tmp_path):
    script = tmp_path / "frozen_like.py"
    script.write_text(
        "import multiprocessing, sys\n"
        "if __name__ == '__main__':\n"
        "    multiprocessing.freeze_support()\n"
        "    multiprocessing.set_start_method('spawn')\n"
        "    from skyframe.engine.parallel_selftest import main\n"
        "    sys.exit(main())\n")
    r = _run_py([str(script)])
    assert r.returncode == 0, r.stdout + r.stderr
