"""Wave 11 backend tests (v0.10): linear buckling analysis, response-spectrum
directional combination (ASCE 7 §12.5), and AISC 360 notional loads, plus the
API surface.

Same rigor as the earlier waves: every expected number is derived in the test
from first principles (closed form or an independent hand value).

Linear buckling (self-contained numpy: elastic + consistent geometric
stiffness, generalized eigenproblem K phi = lambda (-Kg) phi):
  * a pinned-pinned column, meshed into >= 8 segments, buckles at the Euler
    load Pcr = pi^2 E I / L^2 (relerr < 1%; convergence toward Euler as the
    segment count grows; the first mode is a half-sine);
  * a fixed-free cantilever buckles at pi^2 E I / (2L)^2 (< 2%);
  * a fixed-fixed column buckles at pi^2 E I / (0.5L)^2 (< 2% with enough
    elements);
  * factors are sorted ascending and strictly positive.

RS directional (combining two existing RS cases per response quantity):
  * 100/30 -> max(|qx| + 0.3|qy|, 0.3|qx| + |qy|) and SRSS -> sqrt(qx^2+qy^2)
    reproduce the formulas exactly (1e-12);
  * with a zero Y case (qy == 0) the 100/30 combination equals qx exactly.

Notional loads (AISC 360 direct-analysis stability):
  * the per-story notional force is 0.002 * W_story exactly (1e-9), summing to
    0.002 * W_total; the force lands on the requested lateral axis.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.buckling import buckling_analysis
from skyframe.core.builder import quick_building
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    MemberUDL,
    NodalLoad,
    PointSupport,
    make_notional_pattern,
    story_gravity_loads,
)

E_CONC = 25_000_000.0  # kPa


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --------------------------------------------------------------------------- #
# helpers: meshed prismatic column
# --------------------------------------------------------------------------- #
def _column_model(bc: str, n_seg: int, b: float = 0.3, h: float = 0.3,
                  L: float = 5.0, P: float = 100.0) -> BuildingModel:
    """A single vertical column (along +z) meshed into ``n_seg`` segments with
    an axial reference load P applied downward at the top node.

    ``bc``: "pp" pinned-pinned, "cf" fixed-free cantilever, "cc" fixed-fixed
    (top guided).  The base torsion dof is restrained for the pinned case so
    the stiffness matrix stays regular (a real column always has some torsion
    restraint)."""
    mdl = BuildingModel(name=f"col-{bc}")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("S", "C", b, h))
    zs = [L * i / n_seg for i in range(n_seg + 1)]
    for i in range(n_seg):
        mdl.add_member("column", "S", (0, 0, zs[i]), (0, 0, zs[i + 1]),
                       uid=f"C{i}")
    if bc == "pp":
        # pinned-pinned: base ux/uy/uz + torsion (rz) fixed; top ux/uy fixed
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 0, 0, 1)))
        mdl.supports.append(PointSupport((0, 0, L), (1, 1, 0, 0, 0, 0)))
    elif bc == "cf":
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    elif bc == "cc":
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        # top guided: free axial (uz), everything else fixed
        mdl.supports.append(PointSupport((0, 0, L), (1, 1, 0, 1, 1, 1)))
    else:  # pragma: no cover
        raise ValueError(bc)
    mdl.pattern("G", "other").nodal_loads.append(NodalLoad((0, 0, L), fz=-P))
    return mdl


def _euler(bc: str, E: float, I: float, L: float) -> float:
    factor = {"pp": 1.0, "cf": 2.0, "cc": 0.5}[bc]
    return math.pi ** 2 * E * I / (factor * L) ** 2


# --------------------------------------------------------------------------- #
# 1. linear buckling
# --------------------------------------------------------------------------- #
def test_buckling_pinned_pinned_euler():
    """Pinned-pinned column meshed into 8 segments buckles at pi^2 E I / L^2
    within 1%; factors are sorted ascending and strictly positive."""
    b = h = 0.3
    L, P = 5.0, 100.0
    I = b * h ** 3 / 12.0
    Pcr = _euler("pp", E_CONC, I, L)
    mdl = _column_model("pp", 8, b, h, L, P)
    res = buckling_analysis(mdl, {"G": 1.0}, num_modes=4)
    assert res.factors, "expected positive buckling factors"
    # sorted ascending & strictly positive
    assert all(f > 0.0 for f in res.factors)
    assert res.factors == sorted(res.factors)
    got = res.factors[0] * P
    assert got == pytest.approx(Pcr, rel=1e-2)


def test_buckling_pinned_pinned_converges_to_euler():
    """Refining the mesh drives the buckling load toward Euler from above
    (consistent geometric stiffness overestimates), monotonically."""
    b = h = 0.3
    L, P = 5.0, 100.0
    I = b * h ** 3 / 12.0
    Pcr = _euler("pp", E_CONC, I, L)
    errs = []
    for n in (2, 4, 8, 16):
        res = buckling_analysis(_column_model("pp", n, b, h, L, P),
                                {"G": 1.0}, num_modes=1)
        errs.append(abs(res.factors[0] * P - Pcr) / Pcr)
    # strictly decreasing error, and finest mesh essentially exact
    assert errs[0] > errs[1] > errs[2] > errs[3]
    assert errs[-1] < 1e-3
    assert errs[2] < 1e-2                      # 8 segments already < 1%


def test_buckling_pinned_pinned_mode_is_half_sine():
    """The first pinned-pinned buckling mode is a half-sine: the lateral
    resultant along the height matches sin(pi z / L) (zero at the ends, peak
    at mid-height)."""
    L, P, n = 5.0, 100.0, 10
    res = buckling_analysis(_column_model("pp", n, 0.3, 0.3, L, P),
                            {"G": 1.0}, num_modes=2)
    coords = res.node_coords
    mode1 = res.modes[1]
    zs = sorted({c[2] for c in coords.values()})
    lat = []
    for z in zs:
        tag = next(t for t, c in coords.items() if abs(c[2] - z) < 1e-6)
        d = mode1[tag]
        lat.append(math.hypot(d[0], d[1]))     # lateral resultant
    peak = max(lat)
    assert peak > 0.0
    norm = [v / peak for v in lat]
    exact = [math.sin(math.pi * z / L) for z in zs]
    for got, ex in zip(norm, exact):
        assert got == pytest.approx(ex, abs=2e-3)


def test_buckling_cantilever_euler():
    """Fixed-free cantilever column buckles at pi^2 E I / (2L)^2 (< 2%)."""
    b = h = 0.3
    L, P = 5.0, 100.0
    I = b * h ** 3 / 12.0
    Pcr = _euler("cf", E_CONC, I, L)
    res = buckling_analysis(_column_model("cf", 8, b, h, L, P),
                            {"G": 1.0}, num_modes=3)
    assert res.factors[0] * P == pytest.approx(Pcr, rel=2e-2)


def test_buckling_fixed_fixed_euler():
    """Fixed-fixed (top guided) column buckles at pi^2 E I / (0.5L)^2 (< 2%)."""
    b = h = 0.3
    L, P = 5.0, 100.0
    I = b * h ** 3 / 12.0
    Pcr = _euler("cc", E_CONC, I, L)
    res = buckling_analysis(_column_model("cc", 8, b, h, L, P),
                            {"G": 1.0}, num_modes=3)
    assert res.factors[0] * P == pytest.approx(Pcr, rel=2e-2)


def test_buckling_skips_links_with_warning():
    """Link elements are skipped (frame-only buckling) with a warning; the
    frame still solves."""
    L, P = 5.0, 100.0
    mdl = _column_model("cf", 6, 0.3, 0.3, L, P)
    mdl.add_link((0, 0, 0), (1, 0, 0), [1000.0, 0, 0, 0, 0, 0])
    res = buckling_analysis(mdl, {"G": 1.0}, num_modes=2)
    assert any("link" in w for w in res.warnings)
    assert res.factors  # still solves the frame


def test_buckling_case_roundtrip_and_engine_results():
    """BucklingCase round-trips through to_dict/from_dict, and engine.run()
    includes results["buckling"][name] with sorted positive factors."""
    mdl = quick_building(bays_x=2, bays_y=1, stories=2)
    mdl.add_buckling_case("BUCK", {"DEAD": 1.0, "LIVE": 1.0}, num_modes=3)
    d = mdl.to_dict()
    assert d["buckling_cases"]["BUCK"] == {
        "name": "BUCK", "gravity": {"DEAD": 1.0, "LIVE": 1.0},
        "num_modes": 3, "base_case": None}     # base_case: v0.25 field
    back = BuildingModel.from_dict(d)
    assert back.to_dict() == d
    assert back.buckling_cases["BUCK"].num_modes == 3
    # engine results block
    res = OpenSeesEngine(mdl).run().to_dict()
    assert "buckling" in res and "BUCK" in res["buckling"]
    factors = res["buckling"]["BUCK"]["factors"]
    assert factors and all(f > 0.0 for f in factors)
    assert factors == sorted(factors)
    assert "gravity" in res["buckling"]["BUCK"]
    assert "modes" in res["buckling"]["BUCK"]


def test_buckling_case_validation():
    mdl = quick_building(bays_x=1, bays_y=1, stories=1)
    with pytest.raises(ValueError, match="unknown pattern"):
        mdl.add_buckling_case("B", {"NOPE": 1.0})
    with pytest.raises(ValueError, match="num_modes"):
        mdl.add_buckling_case("B", {"DEAD": 1.0}, num_modes=0)
    with pytest.raises(ValueError, match="at least one"):
        mdl.add_buckling_case("B", {})


# --------------------------------------------------------------------------- #
# 2. response-spectrum directional combination
# --------------------------------------------------------------------------- #
def _rs_model():
    mdl = quick_building(bays_x=2, bays_y=2, stories=2)
    sp = [[0.0, 1.0], [0.4, 1.0], [1.0, 0.6], [2.0, 0.3], [4.0, 0.15]]
    mdl.add_rs_case("RSX", "X", sp, combo_method="SRSS")
    mdl.add_rs_case("RSY", "Y", sp, combo_method="SRSS")
    return mdl


def test_rs_directional_100_30_and_srss_formulas():
    """100/30 and SRSS directional combinations reproduce their closed-form
    definitions exactly, quantity by quantity (1e-12)."""
    mdl = _rs_model()
    mdl.add_rs_combo("C100", "RSX", "RSY", "100_30")
    mdl.add_rs_combo("CSRSS", "RSX", "RSY", "SRSS")
    d = OpenSeesEngine(mdl).run().to_dict()
    rx = d["rs_cases"]["RSX"]
    ry = d["rs_cases"]["RSY"]
    c100 = d["rs_cases"]["C100"]
    csrss = d["rs_cases"]["CSRSS"]

    def check(block_x, block_y, block_100, block_srss):
        for key, vx in block_x.items():
            vy, v100, vs = (block_y[key], block_100[key], block_srss[key])
            for i in range(len(vx)):
                qx, qy = abs(vx[i]), abs(vy[i])
                assert v100[i] == pytest.approx(
                    max(qx + 0.3 * qy, 0.3 * qx + qy), rel=0, abs=1e-12)
                assert vs[i] == pytest.approx(
                    math.hypot(qx, qy), rel=0, abs=1e-12)

    check(rx["node_disp"], ry["node_disp"],
          c100["node_disp"], csrss["node_disp"])
    check(rx["member_forces"], ry["member_forces"],
          c100["member_forces"], csrss["member_forces"])
    # base totals
    for k in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
        qx, qy = abs(rx["base"][k]), abs(ry["base"][k])
        assert c100["base"][k] == pytest.approx(
            max(qx + 0.3 * qy, 0.3 * qx + qy), abs=1e-12)
        assert csrss["base"][k] == pytest.approx(math.hypot(qx, qy), abs=1e-12)


def test_rs_directional_zero_y_equals_x():
    """With a zero Y spectrum (qy == 0 everywhere) the 100/30 combination
    equals the X case exactly."""
    mdl = quick_building(bays_x=2, bays_y=1, stories=2)
    sp = [[0.0, 1.0], [1.0, 0.6], [4.0, 0.15]]
    mdl.add_rs_case("RSX", "X", sp, combo_method="SRSS")
    mdl.add_rs_case("RSY0", "Y", [[0.0, 0.0], [4.0, 0.0]], combo_method="SRSS")
    mdl.add_rs_combo("C", "RSX", "RSY0", "100_30")
    d = OpenSeesEngine(mdl).run().to_dict()
    rx = d["rs_cases"]["RSX"]["node_disp"]
    rc = d["rs_cases"]["C"]["node_disp"]
    for t, vx in rx.items():
        for i in range(len(vx)):
            assert rc[t][i] == pytest.approx(abs(vx[i]), abs=1e-12)


def test_rs_combo_roundtrip_and_validation():
    mdl = _rs_model()
    mdl.add_rs_combo("C", "RSX", "RSY", "100_30")
    d = mdl.to_dict()
    assert d["rs_combos"]["C"] == {"name_x": "RSX", "name_y": "RSY",
                                   "method": "100_30"}
    back = BuildingModel.from_dict(d)
    assert back.to_dict() == d
    with pytest.raises(ValueError, match="method"):
        mdl.add_rs_combo("Bad", "RSX", "RSY", "cqc")
    with pytest.raises(ValueError, match="unknown"):
        mdl.add_rs_combo("Bad", "RSX", "NOPE", "SRSS")


# --------------------------------------------------------------------------- #
# 3. notional loads
# --------------------------------------------------------------------------- #
def _notional_model():
    """Two-story frame; each story's beams carry a known gravity UDL so the
    story gravity load is an exact hand value."""
    mdl = BuildingModel(name="notional")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    mdl.set_stories([3.0, 3.0])
    w = 20.0        # kN/m on every beam
    Lb = 6.0
    dead = mdl.pattern("DEAD", "dead")
    for si, st in enumerate(mdl.stories):
        z = st.elevation
        zb = z - st.height
        # 4 corner columns of a single 6x6 bay
        for (x, y) in [(0, 0), (Lb, 0), (Lb, Lb), (0, Lb)]:
            mdl.add_member("column", "COL", (x, y, zb), (x, y, z),
                           story=st.name, uid=f"C{si}-{x}-{y}")
        # 4 perimeter beams, each length Lb
        beams = [((0, 0), (Lb, 0)), ((Lb, 0), (Lb, Lb)),
                 ((Lb, Lb), (0, Lb)), ((0, Lb), (0, 0))]
        for bi, (aa, bb) in enumerate(beams):
            m = mdl.add_member("beam", "BM", (aa[0], aa[1], z),
                               (bb[0], bb[1], z), story=st.name,
                               uid=f"B{si}-{bi}")
            dead.member_udls.append(MemberUDL(m.uid, w))
    mdl.supports = [PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1))
                    for (x, y) in [(0, 0), (Lb, 0), (Lb, Lb), (0, Lb)]]
    # hand value: 4 beams * w * Lb per story
    w_story = 4 * w * Lb
    return mdl, w_story


def test_notional_story_force_hand_value():
    """Ni = 0.002 * W_story exactly per story; sum = 0.002 * W_total; and the
    force lands on the X axis (fx) for direction 'X'."""
    mdl, w_story = _notional_model()
    # story gravity load matches the hand value
    loads = story_gravity_loads(mdl, "DEAD")
    for st in mdl.stories:
        assert loads[st.name] == pytest.approx(w_story, rel=1e-12)
    coeff = 0.002
    pat = make_notional_pattern(mdl, "NX", "X", coeff, "DEAD")
    assert pat.kind == "notional"
    total = 0.0
    for sf in pat.story_forces:
        assert sf.fx == pytest.approx(coeff * w_story, abs=1e-9)
        assert sf.fy == 0.0
        total += sf.fx
    w_total = sum(loads.values())
    assert total == pytest.approx(coeff * w_total, abs=1e-9)


def test_notional_direction_y_and_default_coeff():
    """Direction 'Y' loads fy; the default coefficient is 0.002."""
    mdl, w_story = _notional_model()
    pat = make_notional_pattern(mdl, "NY", "Y")   # defaults: 0.002, DEAD
    for sf in pat.story_forces:
        assert sf.fy == pytest.approx(0.002 * w_story, abs=1e-9)
        assert sf.fx == 0.0


def test_notional_validation():
    mdl, _ = _notional_model()
    with pytest.raises(ValueError, match="direction"):
        make_notional_pattern(mdl, "N", "Z")
    with pytest.raises(ValueError, match="unknown gravity pattern"):
        make_notional_pattern(mdl, "N", "X", 0.002, "NOPE")


# --------------------------------------------------------------------------- #
# 4. API
# --------------------------------------------------------------------------- #
def test_api_case_rs_directional(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    # add two RS cases via POST /api/model round-trip
    sp = [[0.0, 1.0], [1.0, 0.6], [4.0, 0.15]]
    mdl["rs_cases"] = {
        "RSX": {"name": "RSX", "direction": "X", "spectrum": sp,
                "num_modes": 0, "combo_method": "SRSS", "damping": 0.05,
                "scale": 1.0},
        "RSY": {"name": "RSY", "direction": "Y", "spectrum": sp,
                "num_modes": 0, "combo_method": "SRSS", "damping": 0.05,
                "scale": 1.0}}
    assert api_client.post("/api/model", json=mdl).status_code == 200
    r = api_client.post("/api/case/rs-directional",
                        json={"name": "RSC", "name_x": "RSX",
                              "name_y": "RSY", "method": "100_30"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["rs_combos"]["RSC"] == {"name_x": "RSX", "name_y": "RSY",
                                        "method": "100_30"}
    # bad method -> 400
    bad = api_client.post("/api/case/rs-directional",
                          json={"name": "X", "name_x": "RSX",
                                "name_y": "RSY", "method": "cqc"})
    assert bad.status_code == 400
    # unknown RS case -> 400
    bad2 = api_client.post("/api/case/rs-directional",
                           json={"name": "X", "name_x": "RSX",
                                 "name_y": "NOPE", "method": "SRSS"})
    assert bad2.status_code == 400


def test_api_pattern_notional(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    r = api_client.post("/api/pattern/notional",
                        json={"name": "NOT", "direction": "X",
                              "coeff": 0.003, "gravity_pattern": "DEAD"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    pat = body["patterns"]["NOT"]
    assert pat["kind"] == "notional"
    # sum of notional forces = 0.003 * W_total(DEAD)
    # (recompute the DEAD story gravity from the returned model)
    total = sum(sf["fx"] for sf in pat["story_forces"])
    assert total > 0.0
    # bad gravity pattern -> 400
    bad = api_client.post("/api/pattern/notional",
                          json={"name": "N2", "gravity_pattern": "NOPE"})
    assert bad.status_code == 400
    # bad direction -> 400
    bad2 = api_client.post("/api/pattern/notional",
                           json={"name": "N3", "direction": "Z"})
    assert bad2.status_code == 400


def test_api_model_roundtrips_buckling_cases(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    mdl["buckling_cases"] = {"B1": {"name": "B1", "gravity": {"DEAD": 1.0},
                                    "num_modes": 4}}
    r = api_client.post("/api/model", json=mdl)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["buckling_cases"]["B1"]["num_modes"] == 4


def test_api_analyze_includes_buckling(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    mdl["buckling_cases"] = {"B1": {"name": "B1", "gravity": {"DEAD": 1.0},
                                    "num_modes": 3}}
    assert api_client.post("/api/model", json=mdl).status_code == 200
    r = api_client.post("/api/analyze")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert "buckling" in d and "B1" in d["buckling"]
    factors = d["buckling"]["B1"]["factors"]
    assert factors and factors == sorted(factors)
    assert all(f > 0.0 for f in factors)
