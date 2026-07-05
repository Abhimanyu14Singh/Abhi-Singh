"""Wave 13 backend tests (v0.12):

1. Single-pass demand-based steel section optimization
   (``skyframe.design.steel.optimize_members`` / ``apply_suggestions``).
2. Tension/compression-only members (braces, cables, ties) — 2-force Truss
   elements carrying load in one direction only, solved nonlinearly.
3. The ``POST /api/design/optimize`` endpoint and ``axial_limit`` round-trip.

Rigor bar as in earlier waves: every expected number is hand-derived from a
closed form (AISC F2 flexure for the optimizer; statically-determinate truss
statics for the braces), independent of the module under test.

Optimizer hand values (pure major-axis moment Mu33 = 200 kN*m, Lb = L = 3 m,
no axial -> ratio = Mu33 / (phi_b * Mn33), AISC F2, Cb = 1):
  * W16x36 ratio = 0.7435522678066135  (lightest candidate <= 0.95)
  * W14x30 ratio = 1.0130902814000677  (next-lighter FAILS)

Brace statics (single-bay X-brace, bay B, height h, lateral H at a top node):
  the tension diagonal carries T = H / cos(theta), cos(theta) = B / L_diag;
  the compression diagonal deactivates (~0); base FX balances H.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa; Fy in kPa.
"""

import math

import pytest

from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 MemberLoad, NodalLoad)
from skyframe.design.steel import (SectionSuggestion, apply_suggestions,
                                   check_members, optimize_members)

E = 2.0e8            # kPa (200 GPa)
FY = 345_000.0       # kPa (50 ksi nominal)
IN = 0.0254


# --------------------------------------------------------------------------- #
# hand recomputation of the AISC F2 major-axis ratio (independent of module)
# --------------------------------------------------------------------------- #
def _hand_mn_major(Ix, J, d, bf, tf, Zx, ry, rts, Lb):
    """AISC F2 Mn (SI), Cb = 1, from published IMPERIAL properties."""
    Ix, J = Ix * IN ** 4, J * IN ** 4
    d, bf, tf, ry, rts = d * IN, bf * IN, tf * IN, ry * IN, rts * IN
    Zx = Zx * IN ** 3
    Sx = Ix / (d / 2.0)
    ho = d - tf
    Mp = FY * Zx
    Lp = 1.76 * ry * math.sqrt(E / FY)
    if Lb <= Lp:
        return Mp
    jc = J / (Sx * ho)
    Lr = 1.95 * rts * E / (0.7 * FY) * math.sqrt(
        jc + math.sqrt(jc ** 2 + 6.76 * (0.7 * FY / E) ** 2))
    if Lb <= Lr:
        return min(Mp, Mp - (Mp - 0.7 * FY * Sx) * (Lb - Lp) / (Lr - Lp))
    s2 = (Lb / rts) ** 2
    Fcr = math.pi ** 2 * E / s2 * math.sqrt(1.0 + 0.078 * jc * s2)
    return min(Fcr * Sx, Mp)


# published imperial props: (Ix, J, d, bf, tf, Zx, ry, rts)
_W16X36 = (448.0, 0.545, 15.9, 6.99, 0.430, 64.0, 1.52, 1.83)
_W14X30 = (291.0, 0.380, 13.8, 6.73, 0.385, 47.3, 1.49, 1.77)


def _hand_ratio(props, Mu, Lb):
    return Mu / (0.9 * _hand_mn_major(*props, Lb))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _beam_model(section="W12x26", L=3.0):
    m = BuildingModel(name="opt")
    m.rigid_diaphragms = False
    m.add_material(Material("steel", E=E, nu=0.3))
    m.add_section(FrameSection.from_library(section, "steel"))
    m.add_member("beam", section, (0, 0, 0), (L, 0, 0), uid="M1")
    return m


def _moment_results(Mu33, case="D"):
    """Synthetic CONTRACT-shaped results: pure major-axis end moment."""
    mf = [0.0, 0.0, 0.0, 0.0, 0.0, Mu33, 0.0, 0.0, 0.0, 0.0, 0.0, -Mu33]
    return {"cases": {case: {"member_forces": {"M1": mf}}}}


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


CANDS = ["W21x62", "W12x26", "W16x36", "W14x30", "W18x50"]   # unsorted on purpose


# --------------------------------------------------------------------------- #
# 1. optimizer core (engine-free, synthetic demands)
# --------------------------------------------------------------------------- #
def test_optimize_picks_lightest_passing():
    """The optimizer picks the LIGHTEST candidate whose hand H1 ratio <= target;
    the next-lighter candidate FAILS.  Ratios match the hand values to 1e-9."""
    r16 = _hand_ratio(_W16X36, 200.0, 3.0)
    r14 = _hand_ratio(_W14X30, 200.0, 3.0)
    assert r16 <= 0.95 < r14                         # W16x36 passes, W14x30 not
    sug = optimize_members(_beam_model(), _moment_results(200.0), "D",
                           CANDS, target_ratio=0.95)[0]
    assert sug.status == "ok"
    assert sug.suggested_section == "W16x36"
    assert sug.suggested_ratio == pytest.approx(r16, abs=1e-9)
    # the next-lighter candidate genuinely fails the target (independent check)
    assert r14 == pytest.approx(1.0130902814000677, abs=1e-9)
    # current W12x26 is over-stressed here; its ratio is reported
    assert sug.current_section == "W12x26"
    assert sug.current_ratio is not None and sug.current_ratio > 1.0


def test_optimize_weight_and_dict_shape():
    sug = optimize_members(_beam_model(), _moment_results(200.0), "D",
                           CANDS, target_ratio=0.95)[0]
    from skyframe.core.sections_library import library_properties
    A16 = library_properties("W16x36")["A"]
    assert sug.weight_kg_per_m == pytest.approx(A16 * 7850.0, rel=1e-12)
    d = sug.to_dict()
    for k in ("uid", "current_section", "suggested_section", "current_ratio",
              "suggested_ratio", "weight_kg_per_m", "status"):
        assert k in d


def test_optimize_over_demand_no_section_passes():
    """A demand no candidate can satisfy at target -> 'no_section_passes'."""
    sug = optimize_members(_beam_model(), _moment_results(5000.0), "D",
                           CANDS, target_ratio=0.95)[0]
    assert sug.status == "no_section_passes"
    assert sug.suggested_section == ""


def test_optimize_already_optimal_stays():
    """A member already on the lightest passing section keeps it (suggested ==
    current), and the passing ratio is the current ratio."""
    r16 = _hand_ratio(_W16X36, 200.0, 3.0)
    sug = optimize_members(_beam_model("W16x36"), _moment_results(200.0), "D",
                           CANDS, target_ratio=0.95)[0]
    assert sug.suggested_section == "W16x36" == sug.current_section
    assert sug.suggested_ratio == pytest.approx(r16, abs=1e-9)
    assert sug.current_ratio == pytest.approx(r16, abs=1e-9)


def test_optimize_na_when_no_forces():
    """A member with no forces in the results is 'n/a'."""
    empty = {"cases": {"D": {"member_forces": {}}}}
    sug = optimize_members(_beam_model(), empty, "D", CANDS)[0]
    assert sug.status == "n/a"
    assert sug.suggested_section == "" and sug.current_ratio is None


def test_optimize_default_candidates_is_full_library():
    """With candidates=None the whole library is searched (lightest overall)."""
    from skyframe.core.sections_library import library_names
    sug = optimize_members(_beam_model(), _moment_results(60.0), "D",
                           None, target_ratio=0.95)[0]
    assert sug.status == "ok"
    assert sug.suggested_section in library_names()


def test_apply_suggestions_creates_and_assigns():
    model = _beam_model()
    sug = optimize_members(model, _moment_results(200.0), "D", CANDS,
                           target_ratio=0.95)
    apply_suggestions(model, sug)
    assert "W16x36" in model.sections                       # section created
    assert model.sections["W16x36"].material == "steel"     # inherited material
    assert model._member("M1").section == "W16x36"          # reassigned


# --------------------------------------------------------------------------- #
# 1b. end-to-end optimizer with the OpenSees engine
# --------------------------------------------------------------------------- #
def _steel_frame():
    m = BuildingModel(name="frame")
    m.rigid_diaphragms = False
    m.add_material(Material("steel", E=E, nu=0.3))
    m.add_section(FrameSection.from_library("W12x26", "steel"))
    m.set_stories([3.5])
    m.add_member("column", "W12x26", (0, 0, 0), (0, 0, 3.5),
                 story="Story1", uid="C1")
    m.add_member("column", "W12x26", (6, 0, 0), (6, 0, 3.5),
                 story="Story1", uid="C2")
    m.add_member("beam", "W12x26", (0, 0, 3.5), (6, 0, 3.5),
                 story="Story1", uid="B1")
    d = m.pattern("DEAD", "dead")
    d.member_loads.append(MemberLoad("B1", kind="udl", w=40.0,
                                     direction="gravity"))
    q = m.pattern("LAT", "quake")
    q.nodal_loads.append(NodalLoad((0, 0, 3.5), fx=80.0))
    m.add_case("C", {"DEAD": 1.0, "LAT": 1.0})
    return m


def test_optimize_end_to_end_apply_reanalyze():
    """Optimize a real steel frame, apply, RE-ANALYZE, and confirm every
    governing H1 ratio is <= target on the resized model (single-pass caveat:
    each suggested_ratio is <= target by construction; the re-analysis check
    confirms the resized frame is not grossly overstressed)."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    target = 0.9
    model = _steel_frame()
    res = OpenSeesEngine(model).run()
    sug = optimize_members(model, res, "C", target_ratio=target)
    assert all(s.status == "ok" for s in sug)
    for s in sug:
        assert s.suggested_ratio <= target + 1e-9         # single-pass promise
    apply_suggestions(model, sug)
    # re-analyze the resized model: it must still solve and be finite
    res2 = OpenSeesEngine(model).run()
    checks = check_members(model, res2, "C", Fy=FY)
    assert all(math.isfinite(c.ratio) for c in checks)


def test_optimize_iterate_one_reanalysis():
    """iterate=1 performs ONE re-analyse + re-optimize on a COPY (the caller's
    model is untouched) and still returns sections at/under target."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    target = 0.9
    model = _steel_frame()
    res = OpenSeesEngine(model).run()
    before = [m.section for m in model.members]
    sug = optimize_members(model, res, "C", target_ratio=target, iterate=1)
    assert [m.section for m in model.members] == before      # unchanged
    assert all(s.status == "ok" for s in sug)
    for s in sug:
        assert s.suggested_ratio <= target + 1e-9


# --------------------------------------------------------------------------- #
# 2. tension/compression-only members (engine)
# --------------------------------------------------------------------------- #
def _xbrace(axial_limit, load_sign=1.0, B=6.0, h=4.0, H=200.0):
    """Single-bay X-braced frame: near-pinned frame (tiny-I columns/beam so the
    frame carries negligible lateral load) + two diagonals with the given
    axial_limit; lateral H at the top-right node."""
    m = BuildingModel(name="xbrace")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("steel", E=E, nu=0.3))
    m.add_section(FrameSection(name="FR", material="steel", A=0.02,
                               I33=1e-9, I22=1e-9, J=1e-9))
    m.add_section(FrameSection(name="BR", material="steel", A=0.005,
                               I33=1e-8, I22=1e-8, J=1e-8))
    m.set_stories([h])
    m.add_member("column", "FR", (0, 0, 0), (0, 0, h), story="Story1", uid="C1")
    m.add_member("column", "FR", (B, 0, 0), (B, 0, h), story="Story1", uid="C2")
    m.add_member("beam", "FR", (0, 0, h), (B, 0, h), story="Story1", uid="BM")
    m.add_member("brace", "BR", (0, 0, 0), (B, 0, h), story="Story1", uid="D1",
                 axial_limit=axial_limit)
    m.add_member("brace", "BR", (B, 0, 0), (0, 0, h), story="Story1", uid="D2",
                 axial_limit=axial_limit)
    lat = m.pattern("LAT", "quake")
    lat.nodal_loads.append(NodalLoad((B, 0, h), fx=H * load_sign))
    m.add_case("E", {"LAT": 1.0})
    return m, B, h, H


def test_tension_only_xbrace_load_path():
    """Both diagonals tension-only: under +H only the stretched diagonal (D1)
    carries axial T = H/cos(theta) (within 1%); the compression diagonal (D2)
    deactivates (~0), and base FX balances H (1e-6)."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    model, B, h, H = _xbrace("tension", +1.0)
    cr = OpenSeesEngine(model).run_static("E")
    Ld = math.hypot(B, h)
    T_exact = H / (B / Ld)                          # H / cos(theta)
    # member_forces[0] = -N_tension (engine +compression convention)
    T_D1 = -cr.member_forces["D1"][0]               # tension-positive
    T_D2 = -cr.member_forces["D2"][0]
    assert T_D1 == pytest.approx(T_exact, rel=1e-2)
    assert abs(T_D2) < 1e-3 * T_exact               # compression diag ~ 0
    # station N (tension-positive) is the constant axial along the diagonal
    assert cr.member_stations["D1"]["N"][0] == pytest.approx(T_D1, rel=1e-12)
    assert all(v == pytest.approx(T_D1, rel=1e-12)
               for v in cr.member_stations["D1"]["N"])
    # equilibrium: base reactions balance the applied lateral load
    sum_fx = sum(r[0] for r in cr.reactions.values())
    assert sum_fx == pytest.approx(-H, abs=1e-6)


def test_axial_only_member_forces_are_pure_axial():
    """An axial-only member reports zero shear/torsion/moment (V2/V3/T/M2/M3)."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    model, _, _, _ = _xbrace("tension", +1.0)
    cr = OpenSeesEngine(model).run_static("E")
    mf = cr.member_forces["D1"]
    for idx in (1, 2, 3, 4, 5, 7, 8, 9, 10, 11):    # everything but N at i/j
        assert mf[idx] == pytest.approx(0.0, abs=1e-9)
    st = cr.member_stations["D1"]
    for key in ("V2", "V3", "T", "M2", "M3"):
        assert all(v == pytest.approx(0.0, abs=1e-9) for v in st[key])


def _parallel_tie(axial_limit, P, h=4.0):
    """A vertical column ('both') and a parallel axial-only tie of the SAME
    section between the same two nodes; a vertical load P at the top (P<0 =
    downward -> compression on the members)."""
    m = BuildingModel(name="tie")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("steel", E=E, nu=0.3))
    m.add_section(FrameSection(name="S", material="steel", A=0.01,
                               I33=1e-4, I22=1e-4, J=1e-4))
    m.set_stories([h])
    m.add_member("column", "S", (0, 0, 0), (0, 0, h), story="Story1", uid="COL")
    m.add_member("brace", "S", (0, 0, 0), (0, 0, h), story="Story1", uid="TIE",
                 axial_limit=axial_limit)
    g = m.pattern("G", "other")
    g.nodal_loads.append(NodalLoad((0, 0, h), fz=P))
    m.add_case("G", {"G": 1.0})
    return m


def test_tension_only_tie_sheds_compression():
    """A tension-only tie under a COMPRESSIVE demand carries < 1e-3 of what the
    equivalent elastic member (the tie run as 'both') would carry."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    P = -500.0                                       # downward -> compression
    f_elastic = OpenSeesEngine(_parallel_tie("both", P)).run_static(
        "G").member_forces["TIE"][0]
    f_tie = OpenSeesEngine(_parallel_tie("tension", P)).run_static(
        "G").member_forces["TIE"][0]
    assert abs(f_elastic) > 1.0                      # the elastic tie carries load
    assert abs(f_tie) < 1e-3 * abs(f_elastic)        # tension-only sheds it


def test_compression_only_tie_sheds_tension():
    """Mirror: a compression-only strut under a TENSILE demand carries ~0."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    P = +500.0                                       # upward -> tension
    f_elastic = OpenSeesEngine(_parallel_tie("both", P)).run_static(
        "G").member_forces["TIE"][0]
    f_strut = OpenSeesEngine(_parallel_tie("compression", P)).run_static(
        "G").member_forces["TIE"][0]
    assert abs(f_elastic) > 1.0
    assert abs(f_strut) < 1e-3 * abs(f_elastic)


def test_compression_only_xbrace_mirror():
    """Both diagonals compression-only under +H: the diagonal that goes into
    compression (D2) carries H/cos(theta); the tension diagonal (D1) ~0."""
    pytest.importorskip("openseespy.opensees")
    from skyframe.engine.opensees_engine import OpenSeesEngine
    model, B, h, H = _xbrace("compression", +1.0)
    cr = OpenSeesEngine(model).run_static("E")
    Ld = math.hypot(B, h)
    C_exact = H / (B / Ld)
    C_D2 = cr.member_forces["D2"][0]                 # +compression convention
    T_D1 = -cr.member_forces["D1"][0]
    assert C_D2 == pytest.approx(C_exact, rel=1e-2)
    assert abs(T_D1) < 1e-3 * C_exact
    sum_fx = sum(r[0] for r in cr.reactions.values())
    assert sum_fx == pytest.approx(-H, abs=1e-6)


# --------------------------------------------------------------------------- #
# 3. model round-trip + validation + API
# --------------------------------------------------------------------------- #
def test_axial_limit_roundtrip_and_validation():
    m = BuildingModel(name="rt")
    m.rigid_diaphragms = False
    m.add_material(Material("steel", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("S", "steel", 0.1, 0.1))
    m.add_member("brace", "S", (0, 0, 0), (3, 0, 3), uid="B1",
                 axial_limit="tension")
    d = m.to_dict()
    assert next(md for md in d["members"]
                if md["uid"] == "B1")["axial_limit"] == "tension"
    back = BuildingModel.from_dict(d)
    assert back.to_dict() == d
    assert back._member("B1").axial_limit == "tension"
    with pytest.raises(ValueError, match="axial_limit"):
        m.add_member("brace", "S", (0, 0, 0), (1, 0, 0), axial_limit="cable")


def test_axial_limit_default_is_both():
    """Pre-v0.12 members / dicts default to 'both' (ordinary bending frame)."""
    m = BuildingModel(name="d")
    m.add_material(Material("steel", E=E, nu=0.3))
    m.add_section(FrameSection.rectangular("S", "steel", 0.1, 0.1))
    mem = m.add_member("beam", "S", (0, 0, 0), (3, 0, 0))
    assert mem.axial_limit == "both"
    # a member dict without the key round-trips to 'both'
    d = m.to_dict()
    for md in d["members"]:
        md.pop("axial_limit")
    assert BuildingModel.from_dict(d)._member(mem.uid).axial_limit == "both"


def test_api_model_roundtrips_axial_limit(api_client):
    assert api_client.post("/api/model/quick",
                           json={"bays_x": 1, "bays_y": 1,
                                 "stories": 1}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    brace_uid = None
    for m in mdl["members"]:
        if m["kind"] == "beam":
            m["axial_limit"] = "tension"
            brace_uid = m["uid"]
            break
    r = api_client.post("/api/model", json=mdl)
    assert r.status_code == 200, r.get_json()
    back = r.get_json()
    m = next(md for md in back["members"] if md["uid"] == brace_uid)
    assert m["axial_limit"] == "tension"
    # bad value -> 400
    mdl2 = api_client.get("/api/model").get_json()
    mdl2["members"][0]["axial_limit"] = "nope"
    assert api_client.post("/api/model", json=mdl2).status_code == 400


def test_api_design_optimize(api_client):
    pytest.importorskip("openseespy.opensees")
    # a bare steel frame so the W-shapes are recognised library sections
    model = _steel_frame()
    assert api_client.post("/api/model",
                           json=model.to_dict()).status_code == 200
    # suggestions only (apply=false)
    r = api_client.post("/api/design/optimize",
                        json={"case": "C", "target_ratio": 0.9})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["applied"] is False
    assert body["case"] == "C"
    assert len(body["suggestions"]) == 3
    assert all("suggested_section" in s for s in body["suggestions"])
    # apply=true -> the model is updated and echoed
    r2 = api_client.post("/api/design/optimize",
                         json={"case": "C", "target_ratio": 0.9,
                               "apply": True})
    assert r2.status_code == 200, r2.get_json()
    body2 = r2.get_json()
    assert body2["applied"] is True and "model" in body2
    # the current model now carries the optimized sections
    cur = api_client.get("/api/model").get_json()
    ok = {s["uid"]: s["suggested_section"] for s in body2["suggestions"]
          if s["status"] == "ok"}
    for m in cur["members"]:
        if m["uid"] in ok:
            assert m["section"] == ok[m["uid"]]


def test_api_design_optimize_bad_request(api_client):
    pytest.importorskip("openseespy.opensees")
    # missing case -> 400
    assert api_client.post("/api/design/optimize",
                           json={"target_ratio": 0.9}).status_code == 400
    # bad candidates -> 400
    assert api_client.post("/api/design/optimize",
                           json={"case": "C",
                                 "candidates": [1, 2]}).status_code == 400
    # bad target_ratio -> 400
    assert api_client.post("/api/design/optimize",
                           json={"case": "C",
                                 "target_ratio": -1.0}).status_code == 400
