"""Meshed-model performance: parallel solve families, fewer rebuilds,
exact mesh pre-filter.

CONTRACT "Meshed-model performance: parallel solves and fewer rebuilds";
docs/PERFORMANCE.md has the profiles and the before/after table.

Pinned here:

* the parallel run (forked workers on the factored domain) is
  BYTE-IDENTICAL to the serial run (``parallel: "off"``) and to the
  default run, on a meshed slab + wall model above the sparse threshold
  with static cases, combos, modal, two RS cases and CR;
* the deterministic plan (closed form) and the merge by item index; a
  worker that raises or dies has its items re-solved by the parent;
* solve / build counts of a meshed run: ONE domain build, exactly
  ``static cases + massed DOFs + modes`` solves;
* small (band) models never fork and keep the legacy path;
* ``analysis_options.parallel``: validation, serialized only when not the
  default;
* the mesher's bounding-box pre-filter gives exactly the brute-force
  member splits (diagonal brace through wall mesh nodes, closed form).
"""

import copy
import json
import os

import pytest

ops = pytest.importorskip("openseespy.opensees")

from skyframe.core.builder import quick_building
from skyframe.core.model import AreaLoad, BuildingModel, ShellSection
from skyframe.engine import modal_condensed as mcd
from skyframe.engine import parallel as par
from skyframe.engine.opensees_engine import OpenSeesEngine

SPEC = [[0.0, 0.4], [0.5, 1.0], [1.0, 0.6], [2.0, 0.3], [4.0, 0.15]]

needs_fork = pytest.mark.skipif(not par.fork_supported(),
                                reason="process parallelism needs fork")


def _meshed():
    """2-story frame + eccentric meshed wall + slabs, 5 static cases, 4
    combos, 2 RS cases (CQC + SRSS): sparse solver policy."""
    m = quick_building(stories=2, bays_x=2, bays_y=2, bay_width_y=7.0)
    m.add_shell_section(ShellSection("SL", "CONC", 0.2))
    sdl = m.pattern("SDL", "dead")
    for st in m.stories:
        z, zb = st.elevation, st.elevation - st.height
        r = m.add_shell("slab", "shell", "SL",
                        [(0, 0, z), (12, 0, z), (12, 14, z), (0, 14, z)],
                        mesh_size=2.0, story=st.name)
        sdl.area_loads.append(AreaLoad(r.uid, 2.0))
        m.add_shell("wall", "shell", "SL",
                    [(0, 0, zb), (6, 0, zb), (6, 0, z), (0, 0, z)],
                    mesh_size=1.6, story=st.name)
    m.add_case("SDL", {"SDL": 1.0})
    m.add_rs_case("RSX", "X", spectrum=SPEC)
    m.add_rs_case("RSY", "Y", spectrum=SPEC, combo_method="SRSS")
    return m


def _run(mdl, parallel=None):
    m = copy.deepcopy(mdl)
    if parallel is not None:
        m.analysis_options = {"parallel": parallel}
    eng = OpenSeesEngine(m)
    d = eng.run().to_dict()
    return eng, json.dumps(d, sort_keys=True, default=str)


# =========================================================================
# 1. parallel == serial, byte for byte
# =========================================================================
@needs_fork
def test_parallel_run_is_byte_identical_to_serial():
    m = _meshed()
    e_ser, ser = _run(m, "off")
    assert e_ser._asm.linear_system == "UmfPack"
    forks0 = par.stats["forks"]
    e_par, par4 = _run(m, 4)
    assert par.stats["forks"] > forks0           # workers really ran
    assert par4 == ser
    _, default = _run(m)                         # "auto" on this machine
    assert default == ser
    # every family used the workers' results: the prefetched static / RS
    # cases carry their own wall time in the run log
    assert set(e_par.case_times) >= {"DEAD", "LIVE", "SDL", "RSX", "RSY"}
    assert all(t > 0.0 for n, t in e_par.case_times.items()
               if n in ("LIVE", "SDL", "RSY"))


def _rich():
    """_meshed + piers, a P-Delta case between linear ones, a section
    cut, an RS directional combo, linear TH, staged construction, model-
    wide P-Delta and an RS case with its own mode count."""
    m = _meshed()
    m.auto_pier_walls = True
    m.add_case("PD", {"DEAD": 1.0, "EQX": 1.0}, pdelta=True)
    m.add_case("WIND", {"EQY": 0.5})
    m.add_section_cut("C1", "x", 3.0)
    m.add_rs_combo("RSXY", "RSX", "RSY")
    m.add_th_case("TH1", "X", accel=[0.0, 0.1, -0.2, 0.15, 0.0, -0.05] * 3,
                  dt=0.02)
    m.add_staged_case("ST", "DEAD")
    m.pdelta_options = dict(m.pdelta_options, method="non_iterative_mass")
    m.rs_cases["RSY"].num_modes = 4
    return m


@needs_fork
def test_parallel_identical_with_mixed_features():
    m = _rich()
    _, ser = _run(m, "off")
    forks0 = par.stats["forks"]
    _, par4 = _run(m, 4)
    assert par.stats["forks"] > forks0
    assert par4 == ser
    d = json.loads(ser)
    assert {"PD", "WIND"} <= set(d["cases"]) and d["piers"]
    assert {"RSX", "RSY", "RSXY"} <= set(d["rs_cases"])


@needs_fork
def test_parallel_two_lanes_and_env_optout_identical(monkeypatch):
    m = _meshed()
    _, ser = _run(m, "off")
    _, two = _run(m, 2)
    assert two == ser
    monkeypatch.setenv("SKYFRAME_PARALLEL", "0")
    forks0 = par.stats["forks"]
    _, env_off = _run(m, 4)
    assert par.stats["forks"] == forks0          # env var wins
    assert env_off == ser


@needs_fork
def test_worker_failure_is_resolved_serially_with_identical_results(
        monkeypatch):
    """A worker whose solves raise sends an error; the parent re-solves
    its items and the run is unchanged."""
    m = _meshed()
    _, ser = _run(m, "off")
    parent = os.getpid()
    orig = mcd._solve_loads

    def flaky(eng, loads):
        if os.getpid() != parent:
            raise RuntimeError("worker failure (test)")
        return orig(eng, loads)

    monkeypatch.setattr(mcd, "_solve_loads", flaky)
    fb0 = par.stats["fallback_items"]
    _, out = _run(m, 4)
    assert par.stats["fallback_items"] > fb0
    assert out == ser


# =========================================================================
# 2. deterministic plan, merge and fallback (closed form)
# =========================================================================
def test_plan_closed_form():
    # item k -> lane (n - 1 - k) % L ; lane 0 (the parent) gets the last
    assert par.plan(5, 3) == {0: [1, 4], 1: [0, 3], 2: [2]}
    assert par.plan(4, 4) == {0: [3], 1: [2], 2: [1], 3: [0]}
    assert par.plan(2, 8) == {0: [1], 1: [0]}
    assert par.plan(3, 1) == {0: [0, 1, 2]}


@needs_fork
def test_fork_map_merges_by_index_and_recovers_dead_workers():
    items = [1.5 ** k for k in range(11)]
    exact = {k: x * x / 3.0 for k, x in enumerate(items)}
    assert par.fork_map(lambda x: x * x / 3.0, items, 4) == exact
    parent = os.getpid()

    def dies(x):
        if os.getpid() != parent:
            os._exit(3)                          # a crashed worker
        return x * x / 3.0

    fb0 = par.stats["fallback_items"]
    assert par.fork_map(dies, items, 3) == exact
    # lanes 1 and 2 (8 of 11 items) were re-solved by the parent
    lane0 = len(par.plan(len(items), 3)[0])
    assert par.stats["fallback_items"] - fb0 == len(items) - lane0
    assert par.map_family(lambda x: -x, items, 4) == [-x for x in items]
    assert par.map_family(lambda x: -x, items, 4, warm=True) == \
        [-x for x in items]


def test_lanes_policy(monkeypatch):
    class Sparse:
        linear_system = "UmfPack"

    class Band:
        linear_system = "BandGeneral"

    m = quick_building()
    assert par.lanes(m, Band()) == 1             # legacy path: serial
    assert par.lanes(m, None) == 1
    m.analysis_options = {"parallel": "off"}
    assert par.lanes(m, Sparse()) == 1
    if par.fork_supported():
        m.analysis_options = {"parallel": 3}
        assert par.lanes(m, Sparse()) == 3
        m.analysis_options = {}
        monkeypatch.setattr(par, "cores", lambda: 2)
        assert par.lanes(m, Sparse()) == 1       # < 4 cores: serial
        monkeypatch.setattr(par, "cores", lambda: 6)
        assert par.lanes(m, Sparse()) == 6
        monkeypatch.setattr(par, "cores", lambda: 64)
        assert par.lanes(m, Sparse()) == par.MAX_LANES
        monkeypatch.setenv("SKYFRAME_SLOW_PATH", "1")
        assert par.lanes(m, Sparse()) == 1


# =========================================================================
# 3. solve and build counts of a meshed run
# =========================================================================
def test_meshed_run_builds_once_and_solves_exactly_once_each(monkeypatch):
    m = _meshed()
    m.analysis_options = {"parallel": "off"}
    eng = OpenSeesEngine(m)
    counts = {"build": 0, "analyze": 0}
    orig_build = eng._build

    def build(*a, **k):
        counts["build"] += 1
        return orig_build(*a, **k)

    orig_an = ops.analyze

    def analyze(*a, **k):
        counts["analyze"] += 1
        return orig_an(*a, **k)

    eng._build = build
    monkeypatch.setattr(ops, "analyze", analyze)
    res = eng.run()
    n_static = len(m.cases)
    n_massed = len(mcd.massed_dofs(eng._asm))
    n_modes = len(res.modal.periods)
    assert n_static == 5 and n_massed == 6 and n_modes == 6
    # one elastic build for statics, modal (condensed), RS (scaled unit
    # responses) and CR (memo); one solve per static case, per massed DOF
    # and per mode shape — nothing else
    assert counts["build"] == 1
    assert counts["analyze"] == n_static + n_massed + n_modes
    assert len(res.rs_cases) == 2 and res.story_props


def test_small_model_never_forks_and_keeps_legacy_path():
    m = quick_building(stories=3)
    m.add_rs_case("RSX", "X", spectrum=SPEC)
    forks0 = par.stats["forks"]
    e1, d1 = _run(m)
    assert e1._asm.linear_system == "BandGeneral"
    assert par.stats["forks"] == forks0
    _, d2 = _run(m, "off")
    assert d1 == d2


# =========================================================================
# 4. analysis_options.parallel
# =========================================================================
def test_parallel_option_serialized_only_when_not_default():
    m = quick_building()
    m.analysis_options = {"parallel": "auto"}
    assert "analysis_options" not in m.to_dict()
    for v in ("off", 1, 4):
        m.analysis_options = {"parallel": v}
        d = json.loads(json.dumps(m.to_dict()))
        assert d["analysis_options"] == {"parallel": v}
        assert BuildingModel.from_dict(d).analysis_options == {"parallel": v}
        m.validate()


@pytest.mark.parametrize("bad", ["fast", "", 0, 9, -1, True, 2.5, [4]])
def test_parallel_option_validation(bad):
    m = quick_building()
    m.analysis_options = {"parallel": bad}
    with pytest.raises(ValueError):
        m.validate()


# =========================================================================
# 5. exact mesh pre-filter
# =========================================================================
def _braced_wall_model():
    m = quick_building(stories=1, bays_x=1, bays_y=1)
    m.add_shell_section(ShellSection("W", "CONC", 0.25))
    st = m.stories[0]
    z = st.elevation
    m.add_shell("wall", "shell", "W",
                [(0, 0, 0), (6, 0, 0), (6, 0, z), (0, 0, z)],
                mesh_size=1.6, story=st.name)
    sec = next(iter(m.sections))
    m.add_member("brace", sec, (0.0, 0.0, 0.0), (6.0, 0.0, z),
                 story=st.name, uid="BR1")
    return m


def test_mesh_prefilter_equals_brute_force_scan(monkeypatch):
    from skyframe.core import mesh as meshmod
    m = _braced_wall_model()
    z = m.stories[0].elevation
    assert z == pytest.approx(3.2)
    fast = meshmod.mesh_model(m)
    monkeypatch.setattr(meshmod._NearPoints, "candidates",
                        lambda self, member: [int(i) for i in self.idx])
    brute = meshmod.mesh_model(m)
    assert fast.segments == brute.segments
    assert fast.points == brute.points and fast.quads == brute.quads
    # closed form: the 6 x 3.2 wall meshes 4 x 2 (1.5 x 1.6); the
    # diagonal brace passes through the mesh node (3, 0, 1.6) only
    segs = fast.segments["BR1"]
    assert len(segs) == 2
    mid = fast.points[segs[0].nj]
    assert mid == pytest.approx((3.0, 0.0, 1.6))
    half = 0.5 * (6.0 ** 2 + z ** 2) ** 0.5
    assert segs[0].length == pytest.approx(half, abs=1e-6)
