"""``skyframe --selftest-parallel``: prove the spawn worker pool works in
this build (CI runs it inside the frozen Windows binary).

CONTRACT "Windows-capable parallel solves (spawn worker pool)".

Runs a small meshed building (sparse solver policy, static cases, modal by
condensation) twice — serial, then on 2 lanes of the SPAWN worker pool
with the size threshold lifted — and checks that

* spawned workers really solved items (``spawn_pool.stats``);
* every worker passed its probe;
* the two results JSON are byte-identical (SHA-256).

Returns 0 on success, 1 otherwise; prints one JSON summary line.
"""

from __future__ import annotations

import hashlib
import json
import time


def _model():
    from skyframe.core.builder import quick_building
    from skyframe.core.model import AreaLoad, ShellSection
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
    return m


def _sha(model) -> str:
    from skyframe.engine.opensees_engine import OpenSeesEngine
    d = OpenSeesEngine(model).run().to_dict()
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str)
                          .encode()).hexdigest()


def run(lanes: int = 2) -> dict:
    """The self-test; returns the summary dict (``ok`` True/False)."""
    from skyframe.engine import parallel as _par
    from skyframe.engine import spawn_pool as _sp
    t0 = time.perf_counter()
    m = _model()
    m.analysis_options = {"parallel": "off"}
    serial = _sha(m)
    before = dict(_sp.stats)
    saved = _par.SPAWN_EQ_THRESHOLD
    _par.SPAWN_EQ_THRESHOLD = 0            # tiny model: force the pool
    try:
        m = _model()
        m.analysis_options = {"parallel": lanes, "parallel_start": "spawn"}
        spawned = _sha(m)
    finally:
        _par.SPAWN_EQ_THRESHOLD = saved
    # PARDISO + parallel lanes (the Windows desktop default on big models):
    # must run without os.fork and agree with serial to 1e-9 (v1.26.0 bug)
    pardiso = None
    try:
        from skyframe.engine import pardiso_solver as _pds
        if _pds.available():
            from skyframe.core import results_compare as _rc
            from skyframe.engine.opensees_engine import OpenSeesEngine
            _par.SPAWN_EQ_THRESHOLD = 0
            try:
                mp = _model()
                mp.analysis_options = {"parallel": lanes,
                                       "parallel_start": "spawn",
                                       "solver": "pardiso"}
                dp = OpenSeesEngine(mp).run().to_dict()
                ms = _model()
                ms.analysis_options = {"parallel": "off"}
                ds = OpenSeesEngine(ms).run().to_dict()
                _eq, _mm, worst = _rc.compare(ds, dp, rtol=1e-9)
                pardiso = {"ok": worst <= 1e-9, "worst_rel": worst}
            finally:
                _par.SPAWN_EQ_THRESHOLD = saved
    except Exception as exc:                            # noqa: BLE001
        pardiso = {"ok": False, "error": repr(exc)}
    items = _sp.stats["worker_items"] - before["worker_items"]
    bad = _sp.stats["probe_mismatch"] - before["probe_mismatch"]
    fb = _sp.stats["fallback_items"] - before["fallback_items"]
    _sp.shutdown()
    ok = (items > 0 and bad == 0 and fb == 0 and serial == spawned
          and (pardiso is None or pardiso["ok"]))
    return {"ok": ok, "pardiso_parallel": pardiso, "worker_items": items, "probe_mismatch": bad,
            "fallback_items": fb, "identical": serial == spawned,
            "sha256": serial[:16], "seconds": round(time.perf_counter()
                                                    - t0, 2)}


def main() -> int:
    try:
        rep = run()
    except Exception as exc:                            # noqa: BLE001
        rep = {"ok": False, "error": repr(exc)}
    print("SkyFrame parallel self-test " + json.dumps(rep), flush=True)
    return 0 if rep.get("ok") else 1
