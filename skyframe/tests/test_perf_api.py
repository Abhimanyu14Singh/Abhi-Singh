"""v0.26 — engine performance pass + public Python API.

PERFORMANCE (docs/PERF_NOTES.md).  The v0.26 optimization reuses the
assembled OpenSees domain across linear/Newton static case solves and the
center-of-rigidity unit-load solves (``ops.reset()`` + load-pattern removal
instead of a full ``ops.wipe()`` rebuild), hoists repeated dict/index
lookups out of the combo-superposition loops with the EXACT same float
arithmetic, and memoizes pure-geometry member properties.  The contract is
BIT-IDENTITY: with ``SKYFRAME_SLOW_PATH=1`` the engine takes the pre-v0.26
rebuild-per-case path, and this module asserts the two paths' FULL result
dicts are ``==`` on three model shapes (frame-only, shell-heavy,
many-cases) plus a Newton-path (tension-only member) model.

The ONE tolerated exception is ``results["modal"]``: the ARPACK eigen
solver inside OpenSees is not run-to-run deterministic (two runs of the
UNMODIFIED engine already differ in the last float bits / eigenvector
signs — verified before any optimization was written), so modal periods
are compared to 1e-9 relative instead of bit-exact.  The optimization
never touches the modal path (``run_modal`` always does a full rebuild).

PUBLIC API (docs/PUBLIC_API.md): ``skyframe.client`` — the thin scripting
facade.  Every fenced python example in the doc is executed here.
"""

import os
import statistics
import time
from pathlib import Path

import pytest

import skyframe.client as sky
from skyframe.core.model import AreaLoad, ShellSection
from skyframe.engine.opensees_engine import OpenSeesEngine, _slow_path

DOC = Path(__file__).resolve().parents[1] / "docs" / "PUBLIC_API.md"


# --------------------------------------------------------------------------
# the three profile-model shapes (small enough for CI, same structure as
# the docs/PERF_NOTES.md profile models) + a Newton-path model
# --------------------------------------------------------------------------
def model_frame(stories=3):
    """Frame-only: regular moment frame, 4 cases + 4 combos + modal."""
    return sky.quick_building(stories=stories)


def model_shell(mesh=2.0):
    """Shell-heavy: 2-story frame + 2 meshed walls + 2 slabs."""
    mdl = sky.quick_building(name="ShellHeavy", stories=2, bays_x=2,
                             bays_y=2)
    mdl.add_shell_section(ShellSection("W20", "CONC", 0.20))
    mdl.add_shell_section(ShellSection("S15", "CONC", 0.15))
    z1, z2 = mdl.stories[0].elevation, mdl.stories[1].elevation
    mdl.add_shell("wall", "shell", "W20",
                  [(0, 0, 0), (12, 0, 0), (12, 0, z1), (0, 0, z1)],
                  mesh_size=mesh, story=mdl.stories[0].name, uid="WA1")
    mdl.add_shell("wall", "shell", "W20",
                  [(0, 0, z1), (12, 0, z1), (12, 0, z2), (0, 0, z2)],
                  mesh_size=mesh, story=mdl.stories[1].name, uid="WA2")
    mdl.add_shell("slab", "shell", "S15",
                  [(0, 0, z1), (12, 0, z1), (12, 12, z1), (0, 12, z1)],
                  mesh_size=mesh, story=mdl.stories[0].name, uid="SL1")
    mdl.add_shell("slab", "shell", "S15",
                  [(0, 0, z2), (12, 0, z2), (12, 12, z2), (0, 12, z2)],
                  mesh_size=mesh, story=mdl.stories[1].name, uid="SL2")
    mdl.patterns["DEAD"].area_loads.append(AreaLoad("SL1", -5.0))
    mdl.patterns["DEAD"].area_loads.append(AreaLoad("SL2", -5.0))
    return mdl


def model_cases():
    """Many-cases: 2-story frame, 12 static cases + 12 combos."""
    mdl = sky.quick_building(name="ManyCases", stories=2)
    for i in range(8):
        pat = ("DEAD", "LIVE", "EQX", "EQY")[i % 4]
        mdl.add_case(f"X{i + 1}", {pat: 0.5 + 0.1 * i})
    for i in range(8):
        mdl.add_combo(f"CB{i + 1}", {"DEAD": 1.0 + 0.05 * i, "LIVE": 0.5,
                                     ("EQX" if i % 2 else "EQY"): 1.0})
    return mdl


def model_newton():
    """Frame + one tension-only diagonal: the Newton static path."""
    mdl = sky.quick_building(name="NewtonPath", stories=2, bays_x=1,
                             bays_y=1)
    z1 = mdl.stories[0].elevation
    mdl.add_member("brace", "BEAM", (0.0, 0.0, 0.0), (6.0, 0.0, z1),
                   story=mdl.stories[0].name, uid="BR1",
                   axial_limit="tension")
    return mdl


def _run_dict(model, slow, monkeypatch):
    if slow:
        monkeypatch.setenv("SKYFRAME_SLOW_PATH", "1")
    else:
        monkeypatch.delenv("SKYFRAME_SLOW_PATH", raising=False)
    assert _slow_path() is slow
    return OpenSeesEngine(model).run().to_dict()


def _assert_identical_except_modal(slow_d, fast_d):
    assert set(slow_d) == set(fast_d)
    for key in slow_d:
        if key == "modal":
            continue
        assert slow_d[key] == fast_d[key], f"results[{key!r}] diverged"
    pa = slow_d["modal"]["periods"]
    pb = fast_d["modal"]["periods"]
    assert len(pa) == len(pb)
    for a, b in zip(pa, pb):
        assert a == pytest.approx(b, rel=1e-9)


# ------------------------------------------------------- identity per shape
@pytest.mark.parametrize("factory", [model_frame, model_shell, model_cases,
                                     model_newton])
def test_fast_path_bit_identical_to_slow_path(factory, monkeypatch):
    """Full run() dicts: optimized == SKYFRAME_SLOW_PATH=1, bit for bit
    (every case, combo, reaction, station, shell resultant, takedown,
    story prop...), modal periods to 1e-9 (ARPACK nondeterminism is
    upstream and pre-existing — see module docstring)."""
    slow_d = _run_dict(factory(), True, monkeypatch)
    fast_d = _run_dict(factory(), False, monkeypatch)
    _assert_identical_except_modal(slow_d, fast_d)


def test_fast_path_deterministic(monkeypatch):
    """Two optimized runs agree with THEMSELVES exactly (except modal) —
    domain reuse leaks no state between engines."""
    monkeypatch.delenv("SKYFRAME_SLOW_PATH", raising=False)
    a = OpenSeesEngine(model_cases()).run().to_dict()
    b = OpenSeesEngine(model_cases()).run().to_dict()
    _assert_identical_except_modal(a, b)


def test_interleaved_engines_stay_isolated(monkeypatch):
    """Alternating run_static across two live engines must match each
    engine running alone (the reuse token is per-engine)."""
    monkeypatch.delenv("SKYFRAME_SLOW_PATH", raising=False)
    ref_a = OpenSeesEngine(model_frame()).run_static("DEAD").node_disp
    ref_b = OpenSeesEngine(model_cases()).run_static("EQX").node_disp
    ea, eb = OpenSeesEngine(model_frame()), OpenSeesEngine(model_cases())
    da = ea.run_static("DEAD").node_disp
    db = eb.run_static("EQX").node_disp
    da2 = ea.run_static("LIVE")            # back to engine A after B built
    assert da == ref_a
    assert db == ref_b
    solo = OpenSeesEngine(model_frame()).run_static("LIVE").node_disp
    assert da2.node_disp == solo


# ------------------------------------------------------------------- timing
def test_optimized_run_is_faster_than_slow_path(monkeypatch):
    """GENEROUS bound: median-of-3 optimized wall time <= 0.9x the
    slow-path median on the same machine (measured medians are ~0.65x;
    see docs/PERF_NOTES.md — the honest numbers live there, this guard
    only keeps the optimization from silently dying)."""
    def median3(slow):
        if slow:
            monkeypatch.setenv("SKYFRAME_SLOW_PATH", "1")
        else:
            monkeypatch.delenv("SKYFRAME_SLOW_PATH", raising=False)
        mdl = sky.quick_building(stories=8)
        OpenSeesEngine(mdl).run()                       # warm-up
        ts = []
        for _ in range(3):
            t0 = time.perf_counter()
            OpenSeesEngine(mdl).run()
            ts.append(time.perf_counter() - t0)
        return statistics.median(ts)

    t_slow = median3(True)
    t_fast = median3(False)
    assert t_fast <= 0.9 * t_slow, (
        f"optimized median {t_fast:.3f}s vs slow-path {t_slow:.3f}s")


# ----------------------------------------------------------- public API
def test_public_api_surface():
    """Everything promised in docs/PUBLIC_API.md is importable + callable."""
    for name in ("quick_building", "open_model", "save_model", "run",
                 "run_modal", "run_ritz", "run_fna", "run_pushover",
                 "run_cracked", "design_steel", "design_concrete",
                 "design_wall", "design_punching", "to_dataframe"):
        assert name in sky.__all__
        assert callable(getattr(sky, name))


def test_open_save_round_trip(tmp_path):
    mdl = sky.quick_building(stories=2, name="RT")
    p = sky.save_model(mdl, str(tmp_path / "rt.skyframe.json"))
    again = sky.open_model(p)
    assert again.to_dict() == mdl.to_dict()


def test_table_extractor_shapes():
    mdl = sky.quick_building(stories=2)
    res = sky.run(mdl)
    n_cases = len(res.cases) + len(res.combos)

    drifts = sky.to_dataframe(res, "drifts")
    assert len(drifts) == n_cases * len(mdl.stories)
    assert set(drifts[0]) == {"case", "story", "ux", "uy", "drift_x",
                              "drift_y", "shear_x", "shear_y"}
    # drift values are exactly the engine's story block
    r0 = drifts[0]
    assert r0["drift_x"] == res.cases[r0["case"]].story[r0["story"]][
        "drift_x"]

    reac = sky.to_dataframe(res, "reactions")
    n_supports = len(res.supports)
    assert len(reac) == n_cases * n_supports
    assert {"case", "node", "x", "y", "z", "FX", "FZ", "MZ"} <= set(reac[0])
    dead_fz = sum(r["FZ"] for r in reac if r["case"] == "DEAD")
    assert dead_fz == pytest.approx(res.cases["DEAD"].base["FZ"], abs=1e-9)

    mf = sky.to_dataframe(res, "member_forces")
    assert len(mf) == n_cases * len(mdl.members) * 2       # ends i and j
    assert {"case", "member", "story", "end", "N", "V2", "V3", "T",
            "M2", "M3"} == set(mf[0])
    # end-i row of a member reproduces member_forces[:6] exactly
    row = next(r for r in mf if r["case"] == "DEAD" and r["end"] == "i")
    ref = res.cases["DEAD"].member_forces[row["member"]]
    assert [row[k] for k in ("N", "V2", "V3", "T", "M2", "M3")] == ref[:6]

    with pytest.raises(ValueError):
        sky.to_dataframe(res, "nope")
    with pytest.raises(TypeError):
        sky.to_dataframe(res.to_dict(), "drifts")


def test_design_wrappers_and_design_table():
    mdl = sky.quick_building(stories=2)
    res = sky.run(mdl)

    steel = sky.design_steel(mdl, case="DEAD", results=res)
    assert steel["preliminary"] is True
    assert steel["summary"]["n"] == len(steel["checks"]) == len(mdl.members)
    rows = sky.to_dataframe(steel, "design")
    assert rows == steel["checks"]

    rebar = {m.uid: {"n_top": 3, "n_bot": 3, "bar_dia": 0.020}
             for m in mdl.members if m.kind == "beam"}
    conc = sky.design_concrete(mdl, rebar, combos=True, results=res)
    assert len(conc["checks"]) == len(mdl.members)
    beam_rows = [r for r in sky.to_dataframe(conc, "design")
                 if r["uid"] in rebar]
    assert beam_rows and all(r["status"] in ("OK", "NG")
                             for r in beam_rows)

    punch = sky.design_punching(mdl)            # no shell slabs
    assert punch["columns"] == []
    with pytest.raises(KeyError):
        sky.design_punching(mdl, case="NO_SUCH_CASE")
    with pytest.raises(ValueError):
        sky.design_steel(mdl, results=res)      # neither case nor combos
    with pytest.raises(ValueError):
        sky.design_steel(mdl, case="DEAD", code="EC9", results=res)


def test_run_helpers_delegate():
    mdl = sky.quick_building(stories=2)
    modal = sky.run_modal(mdl)
    assert len(modal.periods) == mdl.num_modes
    ritz = sky.run_ritz(mdl, n=2, direction="X")
    assert len(ritz.periods) == 2      # X-reachable subspace: 2 stories
    # facade result == engine result for the same model (static case)
    r_facade = sky.run(mdl)
    r_engine = OpenSeesEngine(mdl).run()
    assert r_facade.cases["DEAD"].to_dict() == \
        r_engine.cases["DEAD"].to_dict()


# ------------------------------------------------- doc examples, executed
def _doc_examples():
    blocks, buf, in_py = [], [], False
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.strip() == "```python":
            in_py, buf = True, []
        elif line.strip() == "```" and in_py:
            in_py = False
            blocks.append("\n".join(buf))
        elif in_py:
            buf.append(line)
    return blocks


def test_public_api_doc_examples():
    """Every fenced python block in docs/PUBLIC_API.md runs, assertions
    included (each in a fresh namespace)."""
    blocks = _doc_examples()
    assert len(blocks) >= 6                     # the doc stays example-rich
    for i, src in enumerate(blocks):
        try:
            exec(compile(src, f"PUBLIC_API.md[block {i}]", "exec"), {})
        except Exception as exc:                # pragma: no cover
            pytest.fail(f"PUBLIC_API.md example block {i} failed: "
                        f"{exc!r}\n---\n{src}")
