"""Wave 8 backend tests (v0.7): self-weight loads, the ASCE 7-16 design
spectrum, auto load-combination generator, and the equivalent-lateral-force
procedure.

Same rigor as test_phase5/test_wave6: every expected number is derived in the
test from first principles or cited against the published ASCE 7-16 tables.

Self-weight:
  * a fixed cantilever column carries base axial FZ = A*L*gamma exactly
    (1e-9);
  * a simply supported beam's self-weight UDL w = A*gamma gives midspan
    M = w L^2/8 from the station diagram (1e-6);
  * a meshed slab's self-weight gives base FZ = area*t*gamma (1e-9);
  * quick_building + a self-weight pattern is in vertical equilibrium
    (base FZ = sum of member weights, base FX = FY = 0) at 1e-9.

ASCE 7-16 spectrum:
  * Fa/Fv cross-checked against Tables 11.4-1/11.4-2 (site D: Fa = 1.0 at
    Ss = 1.5, Fv = 1.7 at S1 = 0.6) plus interpolated columns;
  * SDS/SD1/T0/Ts closed form (1e-9); Sa(Ts) = SDS and Sa(2 Ts) = SD1/(2 Ts)
    (1e-12); monotone up to the plateau then down.

Combinations:
  * quick_building LRFD set equals a hand-written dict exactly; ASD set
    differs correctly; a model without LIVE omits live terms with a warning.

ELF (Section 12.8):
  * Ta, Cs (constant-acceleration branch, SD1 branch, and the Cs floor), V
    and the Fx distribution with the k exponent all match a reference
    reimplementation; story forces sum to V (1e-9).

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import add_self_weight, quick_building
from skyframe.core.codes import (
    apply_asce7_combinations,
    asce7_combinations,
    asce7_elf,
    asce7_spectrum,
    make_rs_case_from_code,
    site_coefficients,
    spectrum_parameters,
)
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (
    G_ACCEL,
    BuildingModel,
    FrameSection,
    Material,
    PointSupport,
    ShellRegion,
    ShellSection,
)

E_CONC = 25_000_000.0  # kPa
GAMMA = 24.0           # kN/m^3


def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _find_node(results: dict, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node found at {pt}")


def _new_model(name, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2, unit_weight=GAMMA))
    mdl.set_stories(list(story_heights))
    return mdl


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --------------------------------------------------------------------------- #
# 1. self-weight loads
# --------------------------------------------------------------------------- #
def test_self_weight_cantilever_column_axial_reaction():
    """A fixed cantilever column carries its own weight: base FZ = A*L*gamma
    exactly (the column self-weight is a distributed axial load)."""
    L, size = 4.0, 0.5
    A = size ** 2
    mdl = _new_model("swcol", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    sw = mdl.pattern("SW", "dead")
    sw.self_weight_factor = 1.0
    mdl.add_case("SW", {"SW": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    base = d["cases"]["SW"]["base"]
    assert base["FZ"] == pytest.approx(A * L * GAMMA, rel=1e-9)
    assert base["FX"] == pytest.approx(0.0, abs=1e-9)
    assert base["FY"] == pytest.approx(0.0, abs=1e-9)


def test_self_weight_simply_supported_beam_midspan_moment():
    """Self-weight UDL w = A*gamma on a simply supported beam: midspan
    M = w L^2 / 8, read from the station diagram (1e-6)."""
    L, b, h = 6.0, 0.3, 0.6
    A = b * h
    w = A * GAMMA
    mdl = _new_model("swbeam", [3.0])
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", b, h))
    mdl.add_member("beam", "BEAM", (0, 0, 0), (L, 0, 0), story="Story1",
                   uid="B1")
    # pin-pin for bending: fix both ends' translations + torsion, free ry/rz
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 0, 0)))
    mdl.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 0, 0)))
    sw = mdl.pattern("SW", "dead")
    sw.self_weight_factor = 1.0
    mdl.add_case("SW", {"SW": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    st = d["cases"]["SW"]["member_stations"]["B1"]
    xs = st["x"]
    mid = min(range(len(xs)), key=lambda i: abs(xs[i] - L / 2.0))
    assert xs[mid] == pytest.approx(L / 2.0, rel=1e-9)
    assert abs(st["M3"][mid]) == pytest.approx(w * L ** 2 / 8.0, rel=1e-6)
    # total base FZ equals the total self-weight w*L (equilibrium)
    assert d["cases"]["SW"]["base"]["FZ"] == pytest.approx(w * L, rel=1e-9)


def test_self_weight_shell_slab_base_reaction():
    """Meshed slab self-weight: base FZ = area * t * gamma exactly (1e-9)."""
    t, W, D = 0.2, 4.0, 3.0
    area = W * D
    mdl = _new_model("swslab", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", t))
    mdl.shells.append(ShellRegion(
        "S1", "slab", "shell", "SH",
        [(0, 0, 3), (W, 0, 3), (W, D, 3), (0, D, 3)],
        mesh_size=1.0, story="Story1"))
    # fully restrain every mesh node so reactions sum to the applied weight
    for p in mesh_model(mdl).points:
        mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
    sw = mdl.pattern("SW", "dead")
    sw.self_weight_factor = 1.0
    mdl.add_case("SW", {"SW": 1.0})
    mdl.num_modes = 0
    d = _run(mdl)
    assert d["cases"]["SW"]["base"]["FZ"] == pytest.approx(
        area * t * GAMMA, rel=1e-9)


def test_self_weight_quick_building_equilibrium_and_helper():
    """add_self_weight builds a pattern + case; quick_building is in vertical
    equilibrium under it: base FZ = sum(A*gamma*L over all members)."""
    qb = quick_building(stories=2, bays_x=2, bays_y=1)
    pat = add_self_weight(qb, pattern="SW", factor=1.0)
    assert pat.self_weight_factor == 1.0 and pat.kind == "dead"
    assert "SW" in qb.cases
    total = 0.0
    for m in qb.members:
        sec = qb.sections[m.section]
        mat = qb.materials[sec.material]
        total += sec.A * mat.unit_weight * m.length
    d = _run(qb)
    base = d["cases"]["SW"]["base"]
    assert base["FZ"] == pytest.approx(total, rel=1e-9)
    assert base["FX"] == pytest.approx(0.0, abs=1e-6)
    assert base["FY"] == pytest.approx(0.0, abs=1e-6)
    # story mass picks up self-weight when SW is in the mass source
    qb.mass_source = {"SW": 1.0}
    masses = qb.compute_story_masses()
    # beams only (columns span stories, excluded like the UDL rule)
    hand = {s.name: 0.0 for s in qb.stories}
    for m in qb.members:
        if m.kind == "column":
            continue
        sec = qb.sections[m.section]
        mat = qb.materials[sec.material]
        hand[m.story] += sec.A * mat.unit_weight * m.length / G_ACCEL
    for s in qb.stories:
        assert masses[s.name] == pytest.approx(hand[s.name], rel=1e-12)


def test_self_weight_pattern_roundtrip():
    mdl = _new_model("swrt", [3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    add_self_weight(mdl, "SW", 1.25)
    d1 = mdl.to_dict()
    assert d1["patterns"]["SW"]["self_weight_factor"] == 1.25
    back = BuildingModel.from_dict(d1)
    assert back.patterns["SW"].self_weight_factor == 1.25
    assert back.to_dict() == d1
    # pre-v0.7 dicts (no self_weight_factor key) default to 0.0
    d1["patterns"]["SW"].pop("self_weight_factor")
    assert BuildingModel.from_dict(d1).patterns["SW"].self_weight_factor == 0.0


# --------------------------------------------------------------------------- #
# 2. ASCE 7-16 design spectrum
# --------------------------------------------------------------------------- #
def test_asce7_site_coefficients_published_values():
    """Fa/Fv against ASCE 7-16 Tables 11.4-1 and 11.4-2."""
    # site D, Ss = 1.5 -> Fa = 1.0 (Table 11.4-1 last column);
    # site D, S1 = 0.6 -> Fv = 1.7 (Table 11.4-2 last column)
    fa, fv = site_coefficients(1.5, 0.6, "D")
    assert fa == pytest.approx(1.0, rel=1e-12)
    assert fv == pytest.approx(1.7, rel=1e-12)
    # interpolation: site D, Ss = 0.6 between 0.5(1.4) and 0.75(1.2)
    fa2, _ = site_coefficients(0.6, 0.3, "D")
    assert fa2 == pytest.approx(1.4 + (1.2 - 1.4) * (0.1 / 0.25), rel=1e-12)
    # interpolation: site D, S1 = 0.15 between 0.1(2.4) and 0.2(2.2)
    _, fv2 = site_coefficients(1.0, 0.15, "D")
    assert fv2 == pytest.approx(2.4 + (2.2 - 2.4) * (0.05 / 0.1), rel=1e-12)
    # clamp below the first / above the last column
    assert site_coefficients(0.1, 0.05, "C") == (1.3, 1.5)
    assert site_coefficients(2.0, 1.0, "B") == (0.9, 0.8)
    with pytest.raises(ValueError, match="site_class"):
        site_coefficients(1.0, 0.4, "Z")


def test_asce7_spectrum_parameters_and_corners():
    """SDS/SD1/T0/Ts closed form; Sa(Ts)=SDS and Sa(2Ts)=SD1/(2Ts)."""
    Ss, S1 = 1.5, 0.6
    sds, sd1, sms, sm1, t0, ts = spectrum_parameters(Ss, S1, "D")
    assert sms == pytest.approx(1.0 * 1.5, rel=1e-12)   # Fa*Ss
    assert sm1 == pytest.approx(1.7 * 0.6, rel=1e-12)   # Fv*S1
    assert sds == pytest.approx(2.0 / 3.0 * 1.5, rel=1e-12)   # 1.0
    assert sd1 == pytest.approx(2.0 / 3.0 * 1.02, rel=1e-12)  # 0.68
    assert t0 == pytest.approx(0.2 * sd1 / sds, rel=1e-12)
    assert ts == pytest.approx(sd1 / sds, rel=1e-12)

    spec = asce7_spectrum(Ss, S1, "D", TL=8.0)

    def sa(T):  # linear interpolation of the returned points, clamped
        pts = sorted((p[0], p[1]) for p in spec)
        if T <= pts[0][0]:
            return pts[0][1]
        if T >= pts[-1][0]:
            return pts[-1][1]
        for (a0, b0), (a1, b1) in zip(pts, pts[1:]):
            if a0 <= T <= a1:
                return b0 + (b1 - b0) * (T - a0) / (a1 - a0)
        return pts[-1][1]

    assert sa(ts) == pytest.approx(sds, rel=1e-12)                 # plateau end
    assert sa(2.0 * ts) == pytest.approx(sd1 / (2.0 * ts), rel=1e-12)
    assert sa(t0) == pytest.approx(sds, rel=1e-12)                 # plateau start
    assert sa(0.0) == pytest.approx(0.4 * sds, rel=1e-12)          # ramp base

    # monotone: rise to the plateau, flat, then descend
    Ts_pts = [p for p in spec]
    vals = [p[1] for p in Ts_pts]
    assert max(vals) == pytest.approx(sds, rel=1e-12)
    rising = [p for p in Ts_pts if p[0] <= t0 + 1e-12]
    for a, b in zip(rising, rising[1:]):
        assert b[1] >= a[1] - 1e-12
    falling = [p for p in Ts_pts if p[0] >= ts - 1e-12]
    for a, b in zip(falling, falling[1:]):
        assert b[1] <= a[1] + 1e-12


def test_make_rs_case_from_code_scaling_and_roundtrip():
    """The code RS case carries the raw spectrum with scale = Ie/R."""
    mdl = quick_building(stories=2)
    rs = make_rs_case_from_code(mdl, "RSX", "X", Ss=1.5, S1=0.6,
                                site_class="D", R=8.0, Ie=1.0)
    assert rs.direction == "X"
    assert rs.scale == pytest.approx(1.0 / 8.0, rel=1e-12)
    assert rs.spectrum == [[float(t), float(v)]
                           for t, v in asce7_spectrum(1.5, 0.6, "D")]
    # Ie/R scaling changes with Ie
    rs2 = make_rs_case_from_code(mdl, "RSY", "Y", Ss=1.5, S1=0.6,
                                 site_class="D", R=6.0, Ie=1.25)
    assert rs2.scale == pytest.approx(1.25 / 6.0, rel=1e-12)
    # round trip
    back = BuildingModel.from_dict(mdl.to_dict())
    assert back.rs_cases["RSX"].scale == pytest.approx(1.0 / 8.0, rel=1e-12)
    assert back.rs_cases["RSX"].spectrum == rs.spectrum
    with pytest.raises(ValueError, match="R must"):
        make_rs_case_from_code(mdl, "B", "X", Ss=1.0, S1=0.4, R=0.0)


# --------------------------------------------------------------------------- #
# 3. auto load-combination generator
# --------------------------------------------------------------------------- #
def test_asce7_lrfd_combinations_dict_equality():
    """quick_building (DEAD, LIVE, EQX, EQY) LRFD set equals a hand dict."""
    qb = quick_building(stories=2)
    combos = asce7_combinations(qb, "LRFD")
    expected = {
        "1.4D": {"DEAD": 1.4},
        "1.2D+1.6L": {"DEAD": 1.2, "LIVE": 1.6},
        "1.2D+1.0L+1.0EQX": {"DEAD": 1.2, "LIVE": 1.0, "EQX": 1.0},
        "1.2D+1.0L-1.0EQX": {"DEAD": 1.2, "LIVE": 1.0, "EQX": -1.0},
        "0.9D+1.0EQX": {"DEAD": 0.9, "EQX": 1.0},
        "0.9D-1.0EQX": {"DEAD": 0.9, "EQX": -1.0},
        "1.2D+1.0L+1.0EQY": {"DEAD": 1.2, "LIVE": 1.0, "EQY": 1.0},
        "1.2D+1.0L-1.0EQY": {"DEAD": 1.2, "LIVE": 1.0, "EQY": -1.0},
        "0.9D+1.0EQY": {"DEAD": 0.9, "EQY": 1.0},
        "0.9D-1.0EQY": {"DEAD": 0.9, "EQY": -1.0},
    }
    assert combos == expected
    # apply_ adds them to the model and they validate
    applied = apply_asce7_combinations(qb, "LRFD")
    assert applied == expected
    for name in expected:
        assert name in qb.combos
        assert qb.combos[name].cases == expected[name]
    qb.validate()


def test_asce7_asd_combinations_differ():
    qb = quick_building(stories=2)
    asd = asce7_combinations(qb, "ASD")
    lrfd = asce7_combinations(qb, "LRFD")
    assert asd != lrfd
    assert "1.4D" not in asd
    assert asd["D"] == {"DEAD": 1.0}
    assert asd["D+L"] == {"DEAD": 1.0, "LIVE": 1.0}
    assert asd["D+0.7EQX"] == {"DEAD": 1.0, "EQX": 0.7}
    assert asd["D-0.7EQX"] == {"DEAD": 1.0, "EQX": -0.7}
    assert asd["0.6D+0.7EQX"] == {"DEAD": 0.6, "EQX": 0.7}
    assert asd["D+0.75L+0.525EQX"] == {"DEAD": 1.0, "LIVE": 0.75,
                                       "EQX": 0.525}
    assert "EQY" in asd["0.6D+0.7EQY"]
    with pytest.raises(ValueError, match="standard"):
        asce7_combinations(qb, "WSD")


def test_asce7_combinations_missing_live_warns():
    """A model with DEAD + EQX but no LIVE omits live terms with a warning."""
    mdl = _new_model("nolive", [3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    mdl.pattern("DEAD", "dead")
    mdl.pattern("EQX", "quake").story_forces.append(
        __import__("skyframe.core.model", fromlist=["StoryForce"])
        .StoryForce("Story1", fx=10.0))
    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_case("EQX", {"EQX": 1.0})
    with pytest.warns(UserWarning, match="LIVE"):
        combos = asce7_combinations(mdl, "LRFD")
    # no combo references a LIVE case; the 1.2D+1.6L combo drops the L term
    for cases in combos.values():
        assert "LIVE" not in cases
    assert combos["1.2D+1.6L"] == {"DEAD": 1.2}
    assert combos["1.2D+1.0L+1.0EQX"] == {"DEAD": 1.2, "EQX": 1.0}
    assert combos["0.9D+1.0EQX"] == {"DEAD": 0.9, "EQX": 1.0}


# --------------------------------------------------------------------------- #
# 4. equivalent lateral force
# --------------------------------------------------------------------------- #
def _elf_ref(masses_by_story, elevs, SDS, SD1, R, Ie, Ct, x, direction):
    """Independent ASCE 7-16 §12.8 reimplementation for cross-checking."""
    hn = max(elevs.values())
    Ta = Ct * hn ** x
    RoIe = R / Ie
    Cs = min(SDS / RoIe, SD1 / (Ta * RoIe))
    Cs = max(Cs, max(0.044 * SDS * Ie, 0.01))
    weights = {s: m * G_ACCEL for s, m in masses_by_story.items()}
    W = sum(weights.values())
    V = Cs * W
    if Ta <= 0.5:
        k = 1.0
    elif Ta >= 2.5:
        k = 2.0
    else:
        k = 1.0 + (Ta - 0.5) / 2.0
    denom = sum(weights[s] * elevs[s] ** k for s in weights)
    Fx = {s: V * weights[s] * elevs[s] ** k / denom for s in weights}
    return Ta, Cs, V, k, Fx


def _elf_model(heights, mass_each):
    mdl = _new_model("elf", heights)
    for s in mdl.stories:
        mdl.story_masses[s.name] = mass_each
    return mdl


@pytest.mark.parametrize("heights,SDS,SD1,R,Ie,regime", [
    ([4.0, 4.0, 4.0], 1.0, 0.6, 8.0, 1.0, "accel"),   # Cs = SDS/(R/Ie)
    ([4.0] * 8, 1.5, 0.9, 6.0, 1.25, "sd1"),          # SD1 branch governs
    ([4.0] * 20, 1.0, 0.6, 8.0, 1.0, "floor"),        # Cs floor governs
])
def test_asce7_elf_hand_calc(heights, SDS, SD1, R, Ie, regime):
    Ct, x = 0.0466, 0.9
    mass_each = 100.0
    mdl = _elf_model(heights, mass_each)
    elevs = {s.name: s.elevation for s in mdl.stories}
    masses = {s.name: mass_each for s in mdl.stories}
    Ta, Cs, V, k, Fx = _elf_ref(masses, elevs, SDS, SD1, R, Ie, Ct, x, "X")

    # confirm the intended governing branch is actually exercised
    RoIe = R / Ie
    if regime == "accel":
        assert Cs == pytest.approx(SDS / RoIe, rel=1e-12)
    elif regime == "sd1":
        assert Cs == pytest.approx(SD1 / (Ta * RoIe), rel=1e-12)
        assert SD1 / (Ta * RoIe) < SDS / RoIe
    else:
        assert Cs == pytest.approx(max(0.044 * SDS * Ie, 0.01), rel=1e-12)

    pat = asce7_elf(mdl, SDS=SDS, SD1=SD1, R=R, Ie=Ie, direction="X",
                    name="EX")
    assert pat.kind == "quake"
    assert mdl.patterns["EX"] is pat
    got = {sf.story: sf.fx for sf in pat.story_forces}
    assert all(sf.fy == 0.0 for sf in pat.story_forces)
    for s in elevs:
        assert got[s] == pytest.approx(Fx[s], rel=1e-9)
    assert sum(got.values()) == pytest.approx(V, rel=1e-9)

    # direction Y populates fy instead
    paty = asce7_elf(mdl, SDS=SDS, SD1=SD1, R=R, Ie=Ie, direction="Y",
                     name="EY")
    gy = {sf.story: sf.fy for sf in paty.story_forces}
    for s in elevs:
        assert gy[s] == pytest.approx(Fx[s], rel=1e-9)
    assert all(sf.fx == 0.0 for sf in paty.story_forces)


def test_asce7_elf_validation():
    mdl = _elf_model([4.0, 4.0], 100.0)
    with pytest.raises(ValueError, match="direction"):
        asce7_elf(mdl, SDS=1.0, SD1=0.6, R=8.0, direction="Z")
    with pytest.raises(ValueError, match="R must"):
        asce7_elf(mdl, SDS=1.0, SD1=0.6, R=0.0)
    with pytest.raises(ValueError, match="SDS must"):
        asce7_elf(mdl, SDS=-1.0, SD1=0.6, R=8.0)
    empty = _new_model("empty", [])
    with pytest.raises(ValueError, match="no stories"):
        asce7_elf(empty, SDS=1.0, SD1=0.6, R=8.0)


# --------------------------------------------------------------------------- #
# 5. API
# --------------------------------------------------------------------------- #
def test_api_v07_endpoints(api_client):
    """All four v0.7 endpoints mutate the current (quick_building) model and
    validate their inputs."""
    # reset the shared module-level model to a fresh quick_building
    assert api_client.post("/api/model/quick", json={}).status_code == 200

    # self-weight
    r = api_client.post("/api/pattern/selfweight",
                        json={"name": "SW", "factor": 1.0})
    assert r.status_code == 200, r.get_json()
    m = r.get_json()
    assert m["patterns"]["SW"]["self_weight_factor"] == 1.0
    assert "SW" in m["cases"]

    # ASCE 7 combos
    r = api_client.post("/api/combos/asce7", json={"standard": "LRFD"})
    assert r.status_code == 200
    assert "1.4D" in r.get_json()["combos"]

    # code RS case
    r = api_client.post("/api/case/rs-code",
                        json={"name": "RSX", "direction": "X", "Ss": 1.5,
                              "S1": 0.6, "site_class": "D", "R": 8.0,
                              "Ie": 1.0})
    assert r.status_code == 200
    rs = r.get_json()["rs_cases"]["RSX"]
    assert rs["scale"] == pytest.approx(1.0 / 8.0, rel=1e-12)

    # ELF pattern
    r = api_client.post("/api/pattern/elf",
                        json={"name": "ELFX", "SDS": 1.0, "SD1": 0.6,
                              "R": 8.0, "Ie": 1.0, "direction": "X"})
    assert r.status_code == 200
    pat = r.get_json()["patterns"]["ELFX"]
    assert pat["kind"] == "quake" and pat["story_forces"]

    # analyze runs the self-weight case that selfweight added
    r = api_client.post("/api/analyze")
    assert r.status_code == 200, r.get_json()
    assert "SW" in r.get_json()["cases"]

    # bad inputs -> 400
    assert api_client.post("/api/case/rs-code",
                           json={"name": "B", "direction": "X",
                                 "S1": 0.6}).status_code == 400   # no Ss
    assert api_client.post("/api/pattern/elf",
                           json={"name": "B", "SD1": 0.6,
                                 "R": 8.0}).status_code == 400     # no SDS
    assert api_client.post("/api/combos/asce7",
                           json={"standard": "WSD"}).status_code == 400
    assert api_client.post("/api/pattern/selfweight",
                           json={"name": "SW",
                                 "factor": "big"}).status_code == 400
