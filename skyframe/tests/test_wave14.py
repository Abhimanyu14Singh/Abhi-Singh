"""Wave 14 backend tests (v0.13):

1. **Section cuts** — force integration across a plane: the resultant
   {FX,FY,FZ,MX,MY,MZ} of all FRAME members crossing the plane, from the
   already-computed 11-station internal forces (pure post-processing).
2. **Named function library** — reusable RS (``SpectrumFunction``) and TH
   (``TimeHistoryFunction``) functions referenced by cases; the Eurocode 8
   Type-1 elastic spectrum preset.
3. **API** — section_cuts / function / preset round-trip through
   ``POST /api/model``; ``POST /api/section-cut``; ``section_cuts`` in results.

Rigor bar as in earlier waves: every expected number is hand-derived from a
closed form or an equilibrium argument, independent of the engine.

Documented section-cut sign convention (CONTRACT v0.13): the resultant is the
internal force that the material on the NEGATIVE-coordinate side of the plane
exerts on the material on the POSITIVE side.  For a horizontal ``z`` cut this
is "force from below supporting above": a downward tip load P on a column
gives ``FZ = +P``; a lateral load +H applied above the cut gives a reported
``FX = -H`` (the shear the lower part feeds the upper part), so
``|FX| = H`` (story-shear equilibrium).  A cantilever cut moment has
``|MY| = P*(L-a)``.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import pytest

from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 Material, MemberLoad, NodalLoad, NodalMass,
                                 PointSupport, SectionCut, ShellRegion,
                                 ShellSection, SpectrumFunction,
                                 TimeHistoryFunction, EC8_TYPE1_GROUND,
                                 eurocode8_damping_correction,
                                 eurocode8_se, eurocode8_spectrum)
from skyframe.engine.opensees_engine import OpenSeesEngine

E = 2.0e8            # kPa (200 GPa)
G_ACCEL = 9.80665


def _run(model):
    return OpenSeesEngine(model).run().to_dict()


# --------------------------------------------------------------------------- #
# 1. Section cuts
# --------------------------------------------------------------------------- #
def _axial_column(P, L=4.0, size=0.3):
    """Vertical column, base fixed, downward tip load P at the top node."""
    m = BuildingModel(name="col")
    m.rigid_diaphragms = False
    m.add_material(Material("s", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("C", "s", size, size))
    m.set_stories([L])
    m.add_member("column", "C", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("D", "dead").nodal_loads.append(NodalLoad((0, 0, L), fz=-P))
    m.add_case("D", {"D": 1.0})
    return m


def test_section_cut_axial_column_FZ_equals_P():
    """A horizontal cut through an axially-loaded column transmits FZ = P
    exactly (the internal axial force = the tip load).  1e-6."""
    P, L = 50.0, 4.0
    m = _axial_column(P, L=L)
    m.add_section_cut("mid", "z", L / 2.0)
    cut = _run(m)["section_cuts"]["D"]["mid"]
    assert cut["n_members"] == 1
    assert cut["n_shells"] == 0
    assert cut["FZ"] == pytest.approx(P, abs=1e-6)
    # a pure axial force: no shear, no moment
    for k in ("FX", "FY", "MX", "MY", "MZ"):
        assert cut[k] == pytest.approx(0.0, abs=1e-6)


def _single_story_frame(H, w, h=3.0, B=5.0, nbays=2):
    """One-story planar frame, columns at x=0..nbays*B, gravity UDL ``w`` on
    the beams and a lateral force ``H`` (split over the top nodes, +x)."""
    m = BuildingModel(name="frame")
    m.rigid_diaphragms = False
    m.add_material(Material("s", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("C", "s", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BM", "s", 0.3, 0.5))
    m.set_stories([h])
    xs = [i * B for i in range(nbays + 1)]
    for i, x in enumerate(xs):
        m.add_member("column", "C", (x, 0, 0), (x, 0, h),
                     story="Story1", uid=f"C{i}")
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    for i in range(nbays):
        m.add_member("beam", "BM", (xs[i], 0, h), (xs[i + 1], 0, h),
                     story="Story1", uid=f"B{i}")
    eq = m.pattern("EQ", "quake")
    for x in xs:
        eq.nodal_loads.append(NodalLoad((x, 0, h), fx=H / len(xs)))
    d = m.pattern("D", "dead")
    for i in range(nbays):
        d.member_loads.append(MemberLoad(f"B{i}", kind="udl", w=w,
                                         direction="gravity"))
    m.add_case("EQ", {"EQ": 1.0})
    m.add_case("D", {"D": 1.0})
    return m, xs


def test_section_cut_story_shear_equilibrium():
    """A cut just above the base sums the column shears to the story shear:
    the reported FX = -H (force the lower part feeds the upper part under a
    +H applied above), i.e. |FX| = H (1e-6)."""
    H, w, h, B, nbays = 30.0, 12.0, 3.0, 5.0, 2
    m, xs = _single_story_frame(H, w, h=h, B=B, nbays=nbays)
    m.add_section_cut("base", "z", 0.001)
    cut = _run(m)["section_cuts"]["EQ"]["base"]
    assert cut["n_members"] == nbays + 1            # every column crosses
    assert cut["FX"] == pytest.approx(-H, abs=1e-6)
    assert abs(cut["FX"]) == pytest.approx(H, abs=1e-6)


def test_section_cut_gravity_weight_above():
    """A mid-height cut carries the full gravity weight ABOVE it: FZ = total
    beam UDL = w * (nbays * B) (1e-6)."""
    H, w, h, B, nbays = 30.0, 12.0, 3.0, 5.0, 2
    m, xs = _single_story_frame(H, w, h=h, B=B, nbays=nbays)
    m.add_section_cut("mid", "z", h / 2.0)
    cut = _run(m)["section_cuts"]["D"]["mid"]
    assert cut["n_members"] == nbays + 1
    assert cut["FZ"] == pytest.approx(w * nbays * B, abs=1e-6)


def test_section_cut_cantilever_moment():
    """A cut at distance a from the fixed end of a tip-loaded cantilever gives
    |MY| = P*(L-a) (the bending moment at the cut) — 1e-4, from linear station
    interpolation.  Also FZ = P (the transmitted shear)."""
    P, L, a = 40.0, 6.0, 2.5
    m = BuildingModel(name="cant")
    m.rigid_diaphragms = False
    m.add_material(Material("s", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("C", "s", 0.3, 0.5))
    m.add_member("beam", "C", (0, 0, 0), (L, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("D", "dead").nodal_loads.append(NodalLoad((L, 0, 0), fz=-P))
    m.add_case("D", {"D": 1.0})
    m.add_section_cut("cut", "x", a)
    cut = _run(m)["section_cuts"]["D"]["cut"]
    assert cut["n_members"] == 1
    assert abs(cut["MY"]) == pytest.approx(P * (L - a), abs=1e-4)
    # documented sign (force from -x side on +x side): MY = -P*(L-a)
    assert cut["MY"] == pytest.approx(-P * (L - a), abs=1e-4)
    assert cut["FZ"] == pytest.approx(P, abs=1e-6)
    for k in ("FX", "FY", "MX", "MZ"):
        assert cut[k] == pytest.approx(0.0, abs=1e-6)


def test_section_cut_bounding_box_ranges():
    """A bounding-box x_range restricts the cut to the members whose crossing
    point falls inside it: two independent axial columns, the ranged cut keeps
    only one (FZ = that column's load)."""
    m = BuildingModel(name="two")
    m.rigid_diaphragms = False
    m.add_material(Material("s", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("C", "s", 0.3, 0.3))
    L = 4.0
    m.set_stories([L])
    P0, P1, B = 20.0, 35.0, 6.0
    for i, (x, P) in enumerate(((0.0, P0), (B, P1))):
        m.add_member("column", "C", (x, 0, 0), (x, 0, L),
                     story="Story1", uid=f"C{i}")
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
        m.pattern("D", "dead").nodal_loads.append(NodalLoad((x, 0, L), fz=-P))
    m.add_case("D", {"D": 1.0})
    m.add_section_cut("all", "z", L / 2.0)
    m.add_section_cut("left", "z", L / 2.0, x_range=[-0.1, 0.1])
    cuts = _run(m)["section_cuts"]["D"]
    assert cuts["all"]["n_members"] == 2
    assert cuts["all"]["FZ"] == pytest.approx(P0 + P1, abs=1e-6)
    assert cuts["left"]["n_members"] == 1
    assert cuts["left"]["FZ"] == pytest.approx(P0, abs=1e-6)


def test_section_cut_shells_counted_but_excluded():
    """Shells crossing the cut are COUNTED (n_shells) and excluded from the
    resultant with a documented warning; the reported FZ is the frame-only
    (column axial) contribution."""
    m = BuildingModel(name="wall")
    m.rigid_diaphragms = False
    m.add_material(Material("s", E=E, nu=0.2, unit_weight=24.0))
    m.add_section(FrameSection.rectangular("C", "s", 0.3, 0.3))
    m.shell_sections["W"] = ShellSection("W", "s", 0.2)
    h, P = 4.0, 25.0
    m.set_stories([h])
    m.add_member("column", "C", (0, 0, 0), (0, 0, h), story="Story1", uid="C1")
    for x in (0, 2, 3, 4, 5, 6):
        m.supports.append(PointSupport((x, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.shells.append(ShellRegion("WALL", "wall", "shell", "W",
                                [(2, 0, 0), (6, 0, 0), (6, 0, h), (2, 0, h)],
                                mesh_size=1.0, story="Story1"))
    m.pattern("D", "dead").nodal_loads.append(NodalLoad((0, 0, h), fz=-P))
    m.add_case("D", {"D": 1.0})
    m.add_section_cut("mid", "z", h / 2.0)
    cut = _run(m)["section_cuts"]["D"]["mid"]
    assert cut["n_members"] == 1
    assert cut["n_shells"] == 1
    assert cut["FZ"] == pytest.approx(P, abs=1e-6)
    assert cut["warnings"] and "EXCLUDED" in cut["warnings"][0]


def test_section_cut_on_additive_combo():
    """Section cuts are computed for additive combos too (same shape)."""
    P, L = 50.0, 4.0
    m = _axial_column(P, L=L)
    m.add_combo("C", {"D": 1.4})
    m.add_section_cut("mid", "z", L / 2.0)
    sc = _run(m)["section_cuts"]
    assert "C" in sc
    assert sc["C"]["mid"]["FZ"] == pytest.approx(1.4 * P, abs=1e-6)


def test_section_cut_roundtrip():
    """SectionCut round-trips (including optional ranges) through the model."""
    m = BuildingModel(name="rt")
    m.add_section_cut("A", "z", 3.25)
    m.add_section_cut("B", "x", 1.0, x_range=[0.0, 5.0], y_range=[-1.0, 1.0])
    d = m.to_dict()
    assert len(d["section_cuts"]) == 2
    m2 = BuildingModel.from_dict(d)
    assert [c.to_dict() for c in m2.section_cuts] == d["section_cuts"]
    b = m2.section_cuts[1]
    assert b.axis == "x" and b.coord == 1.0
    assert b.x_range == [0.0, 5.0] and b.z_range is None


def test_section_cut_validation():
    m = BuildingModel(name="v")
    with pytest.raises(ValueError):
        m.add_section_cut("bad", "w", 1.0)              # bad axis
    with pytest.raises(ValueError):
        m.add_section_cut("bad", "z", 1.0, x_range=[2.0, 1.0])  # lo > hi


# --------------------------------------------------------------------------- #
# 2. Named function library (RS + TH), EC8 preset
# --------------------------------------------------------------------------- #
def _sdof(mass=10.0, L=3.0, size=0.3):
    m = BuildingModel(name="sdof")
    m.rigid_diaphragms = False
    m.add_material(Material("s", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("C", "s", size, size))
    m.set_stories([L])
    m.add_member("column", "C", (0, 0, 0), (0, 0, L), story="Story1", uid="C1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.nodal_masses.append(NodalMass((0, 0, L), mx=mass))
    m.num_modes = 1
    return m


_SPEC = [[0.0, 0.4], [0.3, 0.9], [1.0, 0.3], [3.0, 0.1]]


def test_spectrum_function_equals_inline():
    """An RS case that NAMES a spectrum_function produces bit-identical results
    to an RS case with the same points inline (1e-12)."""
    m_in = _sdof()
    m_in.add_rs_case("RS", "X", _SPEC)
    m_fn = _sdof()
    m_fn.add_spectrum_function("SF", _SPEC)
    m_fn.add_rs_case("RS", "X", function="SF")
    a = _run(m_in)["rs_cases"]["RS"]
    b = _run(m_fn)["rs_cases"]["RS"]
    tip_a = _tip(a)
    tip_b = _tip(b)
    assert b["base"]["FX"] == pytest.approx(a["base"]["FX"], rel=1e-12)
    assert tip_b == pytest.approx(tip_a, rel=1e-12)


def _tip(case):
    return max(v["ux"] for v in case["story"].values())


def test_spectrum_function_missing_errors():
    """A case naming a nonexistent function is rejected with a clear error."""
    m = _sdof()
    # bypass model validation to reach the engine resolver directly
    from skyframe.core.model import ResponseSpectrumCase
    m.rs_cases["RS"] = ResponseSpectrumCase("RS", "X", [], function="NOPE")
    eng = OpenSeesEngine(m)
    with pytest.raises(ValueError) as exc:
        eng.run_response_spectrum("RS")
    assert "NOPE" in str(exc.value)
    # and model.validate() also rejects it (POST /api/model 400 path)
    with pytest.raises(ValueError):
        m.validate()


def test_th_function_shared_by_two_cases():
    """One TH function referenced by two TH cases works, and each equals the
    inline-record case (peak base shear identical)."""
    accel = [0.0, 0.5, -0.4, 0.3, -0.2, 0.1, 0.0, 0.0]
    dt = 0.02
    m = _sdof()
    m.add_th_function("EQ", accel, dt)
    m.add_th_case("TX", "X", function="EQ")
    m.add_th_case("TX2", "X", function="EQ", scale=2.0)
    m.add_th_case("TIN", "X", accel=accel, dt=dt)      # inline reference
    r = _run(m)["th_cases"]
    assert set(r) == {"TX", "TX2", "TIN"}
    px = r["TX"]["peaks"]["base"]["FX"]
    pin = r["TIN"]["peaks"]["base"]["FX"]
    assert px == pytest.approx(pin, rel=1e-12)
    # linear: scale=2 doubles the response exactly
    assert r["TX2"]["peaks"]["base"]["FX"] == pytest.approx(2.0 * px, rel=1e-9)


def test_th_function_missing_errors():
    m = _sdof()
    from skyframe.core.model import TimeHistoryCase
    m.th_cases["T"] = TimeHistoryCase("T", "X", [], 0.0, function="NOPE")
    with pytest.raises(ValueError) as exc:
        OpenSeesEngine(m).run_time_history("T")
    assert "NOPE" in str(exc.value)
    with pytest.raises(ValueError):
        m.validate()


def test_function_library_roundtrip():
    """spectrum_functions / th_functions and case.function round-trip."""
    m = _sdof()
    m.add_spectrum_function("SF", _SPEC, damping=0.03)
    m.add_th_function("TF", [0.0, 0.1, -0.1], 0.01)
    m.add_rs_case("RS", "X", function="SF")
    m.add_th_case("TH", "X", function="TF")
    d = m.to_dict()
    assert d["spectrum_functions"]["SF"]["damping"] == 0.03
    assert d["rs_cases"]["RS"]["function"] == "SF"
    assert d["th_cases"]["TH"]["function"] == "TF"
    m2 = BuildingModel.from_dict(d)
    assert m2.spectrum_functions["SF"].points == [[float(t), float(s)]
                                                  for t, s in _SPEC]
    assert m2.th_functions["TF"].dt == 0.01
    assert m2.rs_cases["RS"].function == "SF"
    assert m2.th_cases["TH"].function == "TF"


# --------------------- Eurocode 8 elastic spectrum preset -------------------
def test_ec8_spectrum_corner_values():
    """EC8 Type-1 elastic spectrum (EN 1998-1 §3.2.2.2) hand-check:

    plateau (TB<=T<=TC) = 2.5*ag*S*eta; the constant-velocity branch value at
    2*TC = plateau*TC/(2TC) = plateau/2.  Corner values to 1e-9.
    """
    ag, gt, damping = 0.35, "C", 0.05
    S, TB, TC, TD = EC8_TYPE1_GROUND[gt]
    eta = eurocode8_damping_correction(damping)
    assert eta == pytest.approx(1.0, abs=1e-12)         # 5% -> eta = 1
    pts = {round(t, 10): sa for t, sa in
           eurocode8_spectrum(ag, gt, damping=damping)}
    plateau = 2.5 * ag * S * eta
    # T = 0 anchors at ag*S; plateau across [TB, TC]; TC->2TC halves it
    assert pts[0.0] == pytest.approx(ag * S, abs=1e-9)
    assert pts[round(TB, 10)] == pytest.approx(plateau, abs=1e-9)
    assert pts[round(TC, 10)] == pytest.approx(plateau, abs=1e-9)
    assert pts[round(2.0 * TC, 10)] == pytest.approx(plateau / 2.0, abs=1e-9)
    assert pts[round(TD, 10)] == pytest.approx(plateau * TC / TD, abs=1e-9)
    # closed-form eval at a constant-displacement point T=3s (> TD)
    se3 = eurocode8_se(3.0, ag, S, TB, TC, TD, eta)
    assert se3 == pytest.approx(plateau * TC * TD / 9.0, abs=1e-12)


def test_ec8_spectrum_usable_as_function():
    """The EC8 preset feeds a SpectrumFunction / RS case unchanged."""
    m = _sdof()
    pts = eurocode8_spectrum(0.3, "B")
    m.add_spectrum_function("EC8", pts)
    m.add_rs_case("RS", "X", function="EC8")
    r = _run(m)["rs_cases"]["RS"]
    assert r["base"]["FX"] > 0.0


# --------------------------------------------------------------------------- #
# 3. API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_api_model_roundtrip(api_client):
    """POST /api/model round-trips section_cuts, functions, and case.function."""
    m = _sdof()
    m.add_spectrum_function("SF", _SPEC)
    m.add_rs_case("RS", "X", function="SF")
    m.add_th_function("TF", [0.0, 0.1, 0.0], 0.01)
    m.add_th_case("TH", "X", function="TF")
    m.add_section_cut("cut", "z", 1.5, x_range=[-1.0, 1.0])
    resp = api_client.post("/api/model", json=m.to_dict())
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["spectrum_functions"]["SF"]["points"]
    assert d["rs_cases"]["RS"]["function"] == "SF"
    assert d["th_cases"]["TH"]["function"] == "TF"
    assert d["section_cuts"][0]["name"] == "cut"
    assert d["section_cuts"][0]["x_range"] == [-1.0, 1.0]


def test_api_model_missing_function_400(api_client):
    """A case that references an undefined function is a 400 (validation)."""
    m = _sdof()
    m.rs_cases["RS"] = __import__(
        "skyframe.core.model", fromlist=["ResponseSpectrumCase"]
    ).ResponseSpectrumCase("RS", "X", [], function="GONE")
    resp = api_client.post("/api/model", json=m.to_dict())
    assert resp.status_code == 400
    assert "GONE" in resp.get_json()["error"]


def test_api_section_cut_endpoint(api_client):
    """POST /api/section-cut adds a cut; bad axis/coord -> 400."""
    ok = api_client.post("/api/section-cut",
                         json={"name": "s1", "axis": "z", "coord": 2.0})
    assert ok.status_code == 200
    assert ok.get_json()["section_cuts"][-1]["name"] == "s1"
    bad_axis = api_client.post("/api/section-cut",
                              json={"name": "b", "axis": "q", "coord": 1.0})
    assert bad_axis.status_code == 400
    no_coord = api_client.post("/api/section-cut",
                              json={"name": "b", "axis": "z"})
    assert no_coord.status_code == 400
    bad_rng = api_client.post(
        "/api/section-cut",
        json={"name": "b", "axis": "z", "coord": 1.0, "x_range": [2.0, 1.0]})
    assert bad_rng.status_code == 400


def test_api_analyze_includes_section_cuts(api_client):
    """POST /api/analyze returns section_cuts for a model that has a cut."""
    m = _axial_column(50.0, L=4.0)
    m.add_section_cut("mid", "z", 2.0)
    assert api_client.post("/api/model", json=m.to_dict()).status_code == 200
    resp = api_client.post("/api/analyze")
    assert resp.status_code == 200
    sc = resp.get_json()["section_cuts"]
    assert sc["D"]["mid"]["FZ"] == pytest.approx(50.0, abs=1e-6)
