"""Regression: Windows has no os.fork.  PARDISO-owned solve families with
parallel lanes must never reach os.fork (bug in v1.26.0: "module 'os' has
no attribute 'fork'" on Windows)."""

import os

import pytest

from skyframe.core import results_compare as rc
from skyframe.engine import parallel as par
from skyframe.engine import pardiso_solver as pds
from skyframe.engine.opensees_engine import OpenSeesEngine
from skyframe.engine.parallel_selftest import _model


def _run(opts):
    m = _model()
    m.analysis_options = opts
    return OpenSeesEngine(m).run().to_dict()


@pytest.mark.skipif(not pds.available(), reason="pypardiso / MKL not installed")
def test_pardiso_parallel_without_fork(monkeypatch):
    ref = _run({"parallel": "off"})
    monkeypatch.setattr(par, "SPAWN_EQ_THRESHOLD", 0)
    monkeypatch.delattr(os, "fork", raising=False)       # what Windows has
    monkeypatch.setattr(par.sys, "platform", "win32")
    got = _run({"parallel": 2, "parallel_start": "spawn", "solver": "pardiso"})
    assert got.get("extra", {}).get("solver", {}).get("system", "").lower().find("pardiso") >= 0 \
        or "pardiso" in str(got.get("extra", {}).get("solver", "")).lower()
    _eq, _mm, worst = rc.compare(ref, got, rtol=1e-9)
    assert worst <= 1e-9, worst
    from skyframe.engine import spawn_pool as sp
    sp.shutdown()


def test_fork_map_serial_without_fork(monkeypatch):
    monkeypatch.delattr(os, "fork", raising=False)
    out = par.fork_map(lambda x: x * x, [1, 2, 3, 4], 4)
    assert out == {0: 1, 1: 4, 2: 9, 3: 16}
