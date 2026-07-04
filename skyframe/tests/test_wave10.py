"""Wave 10 backend tests (v0.9): rigid-end offsets, story-stiffness + seismic
irregularity diagnostics (ASCE 7-16 §12.3), and design-over-all-combinations
envelopes (steel + concrete), plus the API surface.

Same rigor as the earlier waves: every expected number is derived in the test
from first principles (closed form or an independent hand value).

Rigid-end offsets (elastic element over the CLEAR span + very-stiff rigid-link
arms to the real end nodes):
  * a horizontal cantilever with a rigid zone at the FIXED end (rigid_i = a)
    shortens the flexible cantilever to L - rigid_factor*a, so the tip
    deflection under a tip load P is exactly P*(L - rf*a)^3 / (3 E I33) — the
    clean rigid-zone shortening (1e-4, rigid-link precision);
  * rigid_factor = 0 reproduces the plain member exactly (1e-9);
  * a rigid zone at the LOADED (free) end also transfers the load's offset
    moment: the exact tip deflection is the three-term hand value below (1e-4);
  * a fixed-fixed beam with equal end offsets a is stiffer than without — its
    central-point-load midspan deflection is P*Lc^3/(192 E I33) with the clear
    span Lc = L - 2a (direction + hand value, 1e-3).

Diagnostics (pure post-processing of the solved cases):
  * story lateral stiffness k = V_story / Delta matches Sum 12 E I / h^3 on a
    guided single-column shear frame (1e-3);
  * a symmetric diaphragm under EQX has torsional ratio ~ 1.0, flag "none";
  * an eccentric building (stiff columns on one flank) has ratio > 1.2, flag
    "torsional"; a tall soft first story has story-1 stiff_ratio < 0.7 and a
    soft_flag set.

Envelopes:
  * synthetic two-combo results where member A governs under combo1 and member
    B under combo2 -> the envelope picks the right governing combo + ratio per
    member (1e-9); a single-combo envelope equals that combo's checks.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import quick_building
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    NodalLoad,
    PointSupport,
    StoryForce,
)

E_CONC = 25_000_000.0  # kPa


def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _flat_model(name) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.set_stories([5.0])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("S", "C", 0.3, 0.5))
    return mdl


def _node_at(results, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node at {pt}")


def _I33(b, h):
    return b * h ** 3 / 12.0


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# --------------------------------------------------------------------------- #
# 1. rigid-end offsets
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rf", [1.0, 0.5])
def test_rigid_offset_fixed_end_shortens_cantilever(rf):
    """Rigid zone at the FIXED end: the flexible cantilever length drops to
    L - rf*a, so the tip deflection under a tip load P is exactly
    P*(L - rf*a)^3 / (3 E I33) (rigid-link precision, 1e-4)."""
    L, a, P = 5.0, 1.0, 40.0
    b, h = 0.3, 0.5
    EI = E_CONC * _I33(b, h)
    mdl = _flat_model("rc")
    mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1",
                   uid="B1", rigid_i=a, rigid_factor=rf)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((L, 0, 0), fz=-P))
    mdl.add_case("P", {"P": 1.0})
    d = _run(mdl)
    tip = _node_at(d, (L, 0, 0))
    Lc = L - rf * a
    expected = -P * Lc ** 3 / (3.0 * EI)
    assert d["cases"]["P"]["node_disp"][tip][2] == pytest.approx(
        expected, rel=1e-4)


def test_rigid_factor_zero_reproduces_plain_member():
    """rigid_factor = 0 disables the offset: identical to the plain member."""
    L, a, P = 5.0, 1.0, 40.0
    b, h = 0.3, 0.5
    EI = E_CONC * _I33(b, h)

    def tip_uz(**kw):
        mdl = _flat_model("z")
        mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1",
                       uid="B1", **kw)
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        mdl.pattern("P", "other").nodal_loads.append(
            NodalLoad((L, 0, 0), fz=-P))
        mdl.add_case("P", {"P": 1.0})
        d = _run(mdl)
        return d["cases"]["P"]["node_disp"][_node_at(d, (L, 0, 0))][2]

    plain = -P * L ** 3 / (3.0 * EI)
    assert tip_uz() == pytest.approx(plain, rel=1e-9)
    assert tip_uz(rigid_i=a, rigid_j=a, rigid_factor=0.0) == pytest.approx(
        plain, rel=1e-9)


def test_rigid_offset_free_end_transfers_moment():
    """Rigid zone at the LOADED (free) end: the flexible cantilever tip sees
    the load P AND its offset moment M = P*a.  The exact tip deflection is
    P*l^3/3EI + P*a*l^2/EI + P*a^2*l/EI with l = L - a (1e-4)."""
    L, a, P = 5.0, 1.0, 40.0
    b, h = 0.3, 0.5
    EI = E_CONC * _I33(b, h)
    ell = L - a
    mdl = _flat_model("rf")
    mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), story="Story1",
                   uid="B1", rigid_j=a)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((L, 0, 0), fz=-P))
    mdl.add_case("P", {"P": 1.0})
    d = _run(mdl)
    tip = _node_at(d, (L, 0, 0))
    expected = -(P * ell ** 3 / (3.0 * EI)
                 + P * a * ell ** 2 / EI
                 + P * a ** 2 * ell / EI)
    assert d["cases"]["P"]["node_disp"][tip][2] == pytest.approx(
        expected, rel=1e-4)


def test_fixed_fixed_equal_offsets_stiffer():
    """Fixed-fixed beam (two members sharing a midspan node) with equal end
    offsets a: the flexible clear span is Lc = L - 2a, so the central-point-
    load midspan deflection is P*Lc^3/(192 E I33), stiffer than the offset-
    free P*L^3/(192 E I33) (direction + hand value, 1e-3)."""
    L, a, P = 6.0, 1.0, 50.0
    b, h = 0.3, 0.5
    EI = E_CONC * _I33(b, h)
    Lc = L - 2.0 * a
    mdl = _flat_model("ff")
    mdl.add_member("beam", "S", (0, 0, 0), (L / 2, 0, 0), story="Story1",
                   uid="A", rigid_i=a)
    mdl.add_member("beam", "S", (L / 2, 0, 0), (L, 0, 0), story="Story1",
                   uid="B", rigid_j=a)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(NodalLoad((L / 2, 0, 0),
                                                           fz=-P))
    mdl.add_case("P", {"P": 1.0})
    d = _run(mdl)
    mid = _node_at(d, (L / 2, 0, 0))
    got = d["cases"]["P"]["node_disp"][mid][2]
    expected = -P * Lc ** 3 / (192.0 * EI)
    no_offset = -P * L ** 3 / (192.0 * EI)
    assert got == pytest.approx(expected, rel=1e-3)
    assert abs(got) < abs(no_offset)          # stiffer with the rigid zones


def test_rigid_offset_roundtrip_and_validation():
    mdl = _flat_model("rt")
    mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), story="Story1",
                   uid="B1", rigid_i=0.3, rigid_j=0.25, rigid_factor=0.5)
    d = mdl.to_dict()
    md = d["members"][0]
    assert (md["rigid_i"], md["rigid_j"], md["rigid_factor"]) == (0.3, 0.25,
                                                                  0.5)
    back = BuildingModel.from_dict(d)
    m0 = back.members[0]
    assert (m0.rigid_i, m0.rigid_j, m0.rigid_factor) == (0.3, 0.25, 0.5)
    assert back.to_dict() == d
    # pre-v0.9 files (no keys) -> defaults 0/0/1
    md.pop("rigid_i"); md.pop("rigid_j"); md.pop("rigid_factor")
    m1 = BuildingModel.from_dict(d).members[0]
    assert (m1.rigid_i, m1.rigid_j, m1.rigid_factor) == (0.0, 0.0, 1.0)
    # validation: offsets may not eat the whole span, factor in [0, 1]
    with pytest.raises(ValueError, match="no clear span"):
        mdl.add_member("beam", "S", (0, 0, 0), (2, 0, 0), uid="X",
                       rigid_i=1.2, rigid_j=1.0)
    with pytest.raises(ValueError, match="rigid_factor"):
        mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), uid="Y",
                       rigid_factor=1.5)


# --------------------------------------------------------------------------- #
# 2. story stiffness + seismic irregularity
# --------------------------------------------------------------------------- #
def test_story_stiffness_hand_calc():
    """Guided single-column shear frame: the top node's translation ux gives
    k = V/ux = 12 E I / h^3 (fixed-fixed column, 1e-3)."""
    h, s, V = 4.0, 0.4, 100.0
    I = s ** 4 / 12.0
    k_hand = 12.0 * E_CONC * I / h ** 3
    mdl = BuildingModel(name="kframe")
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.set_stories([h])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("COL", "C", s, s))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, h), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    # guided top: free ux (drift) + rz, all else fixed -> fixed-fixed column
    mdl.supports.append(PointSupport((0, 0, h), (0, 1, 1, 1, 1, 0)))
    mdl.pattern("EQX", "quake").nodal_loads.append(NodalLoad((0, 0, h), fx=V))
    mdl.add_case("EQX", {"EQX": 1.0})
    d = _run(mdl)
    kx = d["story_stiffness"]["EQX"]["Story1"]["kx"]
    assert kx == pytest.approx(k_hand, rel=1e-3)


def _diaphragm_frame(name, col_secs):
    """Single-story rigid-diaphragm box (Lx=6, Ly=8) under EQX; ``col_secs``
    maps a corner's y-sign (+1/-1) to a column section name."""
    H = 4.0
    mdl = BuildingModel(name=name)
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.set_stories([H])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("BIG", "C", 0.6, 0.6))
    mdl.add_section(FrameSection.rectangular("SM", "C", 0.4, 0.4))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    xs, ys = [-3.0, 3.0], [-4.0, 4.0]
    i = 0
    for x in xs:
        for y in ys:
            mdl.add_member("column", col_secs(y), (x, y, 0), (x, y, H),
                           story="Story1", uid=f"C{i}")
            i += 1
    pts = [(-3, -4), (3, -4), (3, 4), (-3, 4)]
    for j in range(4):
        aa, bb = pts[j], pts[(j + 1) % 4]
        mdl.add_member("beam", "BM", (aa[0], aa[1], H), (bb[0], bb[1], H),
                       story="Story1", uid=f"B{j}")
    mdl.supports = [PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1))
                    for x in xs for y in ys]
    mdl.pattern("EQX", "quake").story_forces.append(
        StoryForce("Story1", fx=200.0))
    mdl.add_case("EQX", {"EQX": 1.0})
    return mdl


def test_torsional_irregularity_symmetric():
    """Uniform columns -> no diaphragm rotation -> torsional ratio ~ 1.0."""
    mdl = _diaphragm_frame("sym", lambda y: "SM")
    d = _run(mdl)
    irr = d["irregularity"]["EQX"]["Story1"]
    assert irr["tors_ratio_x"] == pytest.approx(1.0, abs=1e-3)
    assert irr["flag"] == "none"


def test_torsional_irregularity_eccentric():
    """Stiff columns on the +Y flank shift the center of rigidity, so an EQX
    story force at the plan center twists the diaphragm: ratio > 1.2."""
    mdl = _diaphragm_frame("ecc", lambda y: "BIG" if y > 0 else "SM")
    d = _run(mdl)
    irr = d["irregularity"]["EQX"]["Story1"]
    assert irr["tors_ratio_x"] > 1.2
    assert irr["flag"] == "torsional"


def test_soft_story_flag():
    """A tall/weak first story is much softer than the story above: story-1
    stiffness ratio < 0.7 and a soft flag is raised (Table 12.3-2)."""
    elevs = [0.0, 5.0, 8.0, 11.0]           # tall soft story 1
    names = ["Story1", "Story2", "Story3"]
    mdl = BuildingModel(name="soft")
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.set_stories([5.0, 3.0, 3.0])
    mdl.num_modes = 0
    mdl.add_section(FrameSection.rectangular("WEAK", "C", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("STIFF", "C", 0.6, 0.6))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    xs, ys = [-3.0, 3.0], [-3.0, 3.0]
    ci = 0
    for si in range(3):
        z0, z1 = elevs[si], elevs[si + 1]
        sec = "WEAK" if si == 0 else "STIFF"
        for x in xs:
            for y in ys:
                mdl.add_member("column", sec, (x, y, z0), (x, y, z1),
                               story=names[si], uid=f"C{ci}")
                ci += 1
        pts = [(-3, -3), (3, -3), (3, 3), (-3, 3)]
        for j in range(4):
            aa, bb = pts[j], pts[(j + 1) % 4]
            mdl.add_member("beam", "BM", (aa[0], aa[1], z1), (bb[0], bb[1], z1),
                           story=names[si], uid=f"B{si}_{j}")
    mdl.supports = [PointSupport((x, y, 0), (1, 1, 1, 1, 1, 1))
                    for x in xs for y in ys]
    p = mdl.pattern("EQX", "quake")
    for sn in names:
        p.story_forces.append(StoryForce(sn, fx=50.0))
    mdl.add_case("EQX", {"EQX": 1.0})
    d = _run(mdl)
    s1 = d["irregularity"]["EQX"]["Story1"]
    assert s1["stiff_ratio"] < 0.7
    assert s1["soft_flag"] in ("soft", "extreme_soft")
    # the stiffness is genuinely V/Delta: story-1 k below story-2 k
    k1 = d["story_stiffness"]["EQX"]["Story1"]["kx"]
    k2 = d["story_stiffness"]["EQX"]["Story2"]["kx"]
    assert s1["stiff_ratio"] == pytest.approx(k1 / k2, rel=1e-9)


# --------------------------------------------------------------------------- #
# 3. design over all load combinations (envelope)
# --------------------------------------------------------------------------- #
def _steel_two_beam_model():
    from skyframe.design.steel import E_STEEL
    mdl = BuildingModel(name="steel")
    mdl.add_material(Material("STEEL", E=E_STEEL))
    mdl.add_section(FrameSection.from_library("W12x26", "STEEL"))
    mdl.add_member("beam", "W12x26", (0, 0, 0), (3, 0, 0), uid="A")
    mdl.add_member("beam", "W12x26", (0, 3, 0), (3, 3, 0), uid="B")
    return mdl


def _mf_mz(mz):
    return [0.0, 0.0, 0.0, 0.0, 0.0, mz, 0.0, 0.0, 0.0, 0.0, 0.0, mz]


def test_steel_envelope_picks_governing_combo():
    from skyframe.design.steel import (check_members,
                                       check_members_envelope, summarize)
    mdl = _steel_two_beam_model()
    results = {"combos": {
        "C1": {"member_forces": {"A": _mf_mz(220.0), "B": _mf_mz(10.0)}},
        "C2": {"member_forces": {"A": _mf_mz(10.0), "B": _mf_mz(240.0)}},
    }}
    c1 = {c.uid: c for c in check_members(mdl, results, "C1")}
    c2 = {c.uid: c for c in check_members(mdl, results, "C2")}
    env = {c.uid: c for c in check_members_envelope(mdl, results)}
    # member A governs under C1, member B under C2
    assert env["A"].governing_combo == "C1"
    assert env["A"].ratio == pytest.approx(c1["A"].ratio, rel=1e-9)
    assert env["B"].governing_combo == "C2"
    assert env["B"].ratio == pytest.approx(c2["B"].ratio, rel=1e-9)
    # summarize works on the envelope list
    summ = summarize(list(env.values()))
    assert summ["n"] == 2
    assert summ["max_ratio"] == pytest.approx(
        max(env["A"].ratio, env["B"].ratio), rel=1e-9)


def test_steel_envelope_single_combo_equals_that_combo():
    from skyframe.design.steel import check_members, check_members_envelope
    mdl = _steel_two_beam_model()
    results = {"combos": {
        "C1": {"member_forces": {"A": _mf_mz(220.0), "B": _mf_mz(10.0)}},
        "C2": {"member_forces": {"A": _mf_mz(10.0), "B": _mf_mz(240.0)}},
    }}
    plain = {c.uid: c for c in check_members(mdl, results, "C1")}
    env = {c.uid: c for c in
           check_members_envelope(mdl, results, combos=["C1"])}
    for uid in ("A", "B"):
        assert env[uid].governing_combo == "C1"
        assert env[uid].ratio == pytest.approx(plain[uid].ratio, rel=1e-12)
        assert env[uid].status == plain[uid].status
        assert env[uid].Mu33 == pytest.approx(plain[uid].Mu33, rel=1e-12)


def test_concrete_envelope_picks_governing_combo():
    from skyframe.design.concrete import (RebarLayout,
                                          check_concrete_members,
                                          check_concrete_members_envelope)
    mdl = BuildingModel(name="conc")
    mdl.add_material(Material("C", E=E_CONC))
    mdl.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.5))
    mdl.add_member("beam", "BM", (0, 0, 0), (4, 0, 0), uid="A")
    mdl.add_member("beam", "BM", (0, 4, 0), (4, 4, 0), uid="B")
    rebar = {"A": RebarLayout(3, 3, 0.02), "B": RebarLayout(3, 3, 0.02)}
    results = {"combos": {
        "G1": {"member_forces": {"A": _mf_mz(180.0), "B": _mf_mz(20.0)}},
        "G2": {"member_forces": {"A": _mf_mz(20.0), "B": _mf_mz(190.0)}},
    }}
    g1 = {c.uid: c for c in check_concrete_members(mdl, results, "G1", rebar)}
    g2 = {c.uid: c for c in check_concrete_members(mdl, results, "G2", rebar)}
    env = {c.uid: c
           for c in check_concrete_members_envelope(mdl, results, rebar)}
    assert env["A"].governing_combo == "G1"
    assert env["A"].ratio == pytest.approx(g1["A"].ratio, rel=1e-9)
    assert env["B"].governing_combo == "G2"
    assert env["B"].ratio == pytest.approx(g2["B"].ratio, rel=1e-9)


# --------------------------------------------------------------------------- #
# 4. API
# --------------------------------------------------------------------------- #
def test_api_design_concrete_combos_mode(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    combos = list(mdl["combos"].keys())
    rebar = {m["uid"]: {"n_top": 3, "n_bot": 3, "bar_dia": 0.02}
             for m in mdl["members"]}
    r = api_client.post("/api/design/concrete",
                        json={"combos": True, "rebar": rebar})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["combos"] is True
    checked = [c for c in body["checks"] if c["ratio"] is not None]
    assert checked, "expected at least one rated concrete member"
    for c in checked:
        assert c["governing_combo"] in combos


def test_api_design_steel_named_combos_mode(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    combos = list(mdl["combos"].keys())
    r = api_client.post("/api/design/steel", json={"combos": combos[:2]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    # every check carries the envelope tag (quick_building sections are
    # rectangular -> N/A, but the field must round-trip)
    for c in body["checks"]:
        assert "governing_combo" in c
    # missing both case and combos -> 400
    assert api_client.post("/api/design/steel", json={}).status_code == 400


def test_api_model_roundtrips_rigid_offsets(api_client):
    mdl = _flat_model("apirig")
    mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0), story="Story1",
                   uid="B1", rigid_i=0.4, rigid_j=0.2, rigid_factor=0.75)
    r = api_client.post("/api/model", json=mdl.to_dict())
    assert r.status_code == 200, r.get_json()
    md = r.get_json()["members"][0]
    assert md["rigid_i"] == 0.4
    assert md["rigid_j"] == 0.2
    assert md["rigid_factor"] == 0.75


def test_api_analyze_returns_diagnostics(api_client):
    assert api_client.post("/api/model/quick", json={}).status_code == 200
    r = api_client.post("/api/analyze")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert "story_stiffness" in d
    assert "irregularity" in d
    # EQX is a lateral case -> it must appear with per-story kx/ky entries
    assert "EQX" in d["story_stiffness"]
    any_story = next(iter(d["story_stiffness"]["EQX"].values()))
    assert "kx" in any_story and "ky" in any_story
    entry = next(iter(d["irregularity"]["EQX"].values()))
    assert {"tors_ratio_x", "tors_ratio_y", "flag", "stiff_ratio",
            "soft_flag"} <= set(entry.keys())
