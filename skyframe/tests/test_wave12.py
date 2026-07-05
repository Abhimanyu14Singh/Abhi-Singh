"""Wave 12 backend tests (v0.11): Winkler elastic-foundation line springs on
members and the gravity load takedown / support-reaction summary, plus the API
surface.

Same rigor as earlier waves: every expected number is hand-derived from a
closed form.

Beam on elastic foundation (Hetenyi, infinite-beam closed forms; a central
point load P, beta = (k_line / (4 E I33))^0.25):
  * max deflection  y_max = P * beta / (2 * k_line);
  * max moment      M_max = P / (4 * beta);
  both matched within 3% at the automatic (fine) discretization, and the
  error converges monotonically toward the closed form as the segment count N
  grows (the discretization is forced via the mesh module globals).
  * a rigid-ish short footing under a central load settles nearly uniformly:
    w = P / (k_line * L) (1%);
  * equilibrium: the sum of the soil-spring reactions equals the applied
    downward load exactly.

Gravity load takedown:
  * quick_building DEAD -> sum of support FZ == total applied dead load, each
    support labelled by its grid intersection ("A-1" ...), balance_ok True;
  * a 1.2D + 1.6L combo -> per-support FZ == 1.2*deadFZ + 1.6*liveFZ (exact
    linear superposition).

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

import skyframe.core.mesh as mesh
from skyframe.core.builder import quick_building
from skyframe.core.model import (
    BuildingModel,
    FrameSection,
    Material,
    MemberLoad,
    PointSupport,
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
# helpers: a beam resting on a Winkler bed, central point load
# --------------------------------------------------------------------------- #
def _beam_on_foundation(L: float, b: float, h: float, ks: float, width: float,
                        P: float, E: float = E_CONC) -> BuildingModel:
    """A horizontal beam along +x on a Winkler soil bed with a central
    downward point load P.  The vertical (uz) and bending (ry) DOFs are left
    free so the soil springs + beam bending carry the load; the out-of-plane
    and axial DOFs are pinned at the ends to remove the rigid-body modes
    (they carry no vertical load, so y_max / M_max are unaffected)."""
    mdl = BuildingModel(name="beam-on-soil")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 0
    mdl.add_material(Material("C", E=E, nu=0.2))
    mdl.add_section(FrameSection.rectangular("S", "C", b, h))
    mdl.add_member("beam", "S", (0, 0, 0), (L, 0, 0), uid="BM",
                   foundation_ks=ks, foundation_width=width)
    pat = mdl.pattern("G", "other")
    pat.member_loads.append(
        MemberLoad("BM", kind="point", w=P, a=0.5, direction="gravity"))
    # fix ux, uy, rx, rz at both ends; leave uz + ry free
    mdl.supports = [PointSupport((0, 0, 0), (1, 1, 0, 1, 0, 1)),
                    PointSupport((L, 0, 0), (1, 1, 0, 1, 0, 1))]
    mdl.add_case("G", {"G": 1.0})
    return mdl


def _solve_beam(mdl):
    res = OpenSeesEngine(mdl).run()
    cr = res.cases["G"]
    y_max = max(abs(d[2]) for d in cr.node_disp.values())     # max |uz|
    st = cr.member_stations["BM"]
    m_max = max(abs(v) for v in st["M3"])                     # major-axis M
    return y_max, m_max, res


# --------------------------------------------------------------------------- #
# 1. Winkler beam on elastic foundation
# --------------------------------------------------------------------------- #
def test_beam_on_elastic_foundation_closed_form():
    """Long beam (L >> lc), central point load: y_max and M_max match the
    Hetenyi infinite-beam closed forms within 3% at automatic discretization."""
    L, b, h = 24.0, 0.5, 0.5
    ks, width, P = 20_000.0, 1.0, 100.0
    I33 = b * h ** 3 / 12.0
    k_line = ks * width
    beta = (k_line / (4.0 * E_CONC * I33)) ** 0.25
    lc = 1.0 / beta
    assert L / lc > 8.0                        # genuinely "long"
    y_cf = P * beta / (2.0 * k_line)
    m_cf = P / (4.0 * beta)
    y_max, m_max, _ = _solve_beam(
        _beam_on_foundation(L, b, h, ks, width, P))
    assert y_max == pytest.approx(y_cf, rel=3e-2)
    assert m_max == pytest.approx(m_cf, rel=3e-2)


def test_beam_on_elastic_foundation_converges(monkeypatch):
    """Refining the soil-spring discretization drives BOTH y_max and M_max
    toward the closed form, monotonically (error strictly decreasing)."""
    L, b, h = 24.0, 0.5, 0.5
    ks, width, P = 20_000.0, 1.0, 100.0
    I33 = b * h ** 3 / 12.0
    k_line = ks * width
    beta = (k_line / (4.0 * E_CONC * I33)) ** 0.25
    y_cf = P * beta / (2.0 * k_line)
    m_cf = P / (4.0 * beta)
    y_err, m_err = [], []
    for n in (4, 8, 16, 32):
        # force exactly n equal segments
        monkeypatch.setattr(mesh, "FOUNDATION_MIN_SEGMENTS", n)
        monkeypatch.setattr(mesh, "FOUNDATION_MAX_SEGMENTS", n)
        y_max, m_max, _ = _solve_beam(
            _beam_on_foundation(L, b, h, ks, width, P))
        y_err.append(abs(y_max - y_cf) / y_cf)
        m_err.append(abs(m_max - m_cf) / m_cf)
    assert y_err[0] > y_err[1] > y_err[2] > y_err[3]
    assert m_err[0] > m_err[1] > m_err[2] > m_err[3]
    assert y_err[-1] < 3e-2 and m_err[-1] < 3e-2


def test_beam_on_elastic_foundation_equilibrium():
    """The soil-spring reactions balance the applied downward load exactly
    (sum of support FZ == P), and the takedown reports balance_ok."""
    L, b, h = 24.0, 0.5, 0.5
    ks, width, P = 20_000.0, 1.0, 100.0
    mdl = _beam_on_foundation(L, b, h, ks, width, P)
    res = OpenSeesEngine(mdl).run()
    cr = res.cases["G"]
    sum_fz = sum(r[2] for r in cr.reactions.values())
    assert sum_fz == pytest.approx(P, abs=1e-6)
    td = res.takedown["G"]
    assert td["applied_FZ"] == pytest.approx(P, abs=1e-9)
    assert td["total_FZ"] == pytest.approx(P, abs=1e-6)
    assert td["balance_ok"] is True


def test_rigid_footing_uniform_settlement():
    """A very stiff short footing under a central load settles nearly
    uniformly at w = P / (k_line * L) (within 1%)."""
    L, b, h = 3.0, 0.6, 0.6
    ks, width, P = 30_000.0, 1.5, 500.0
    k_line = ks * width
    settle = P / (k_line * L)
    mdl = _beam_on_foundation(L, b, h, ks, width, P, E=1.0e12)
    res = OpenSeesEngine(mdl).run()
    cr = res.cases["G"]
    uz = [abs(d[2]) for d in cr.node_disp.values()]
    assert min(uz) == pytest.approx(settle, rel=1e-2)
    assert max(uz) == pytest.approx(settle, rel=1e-2)
    # essentially uniform (rigid footing)
    assert (max(uz) - min(uz)) / settle < 1e-2


def test_foundation_segment_count_formula():
    """The discretization obeys segment length <= min(L/8, lc/4), capped 40."""
    from skyframe.core.mesh import foundation_segment_count
    L, b, h = 24.0, 0.5, 0.5
    ks, width = 20_000.0, 1.0
    mdl = _beam_on_foundation(L, b, h, ks, width, 100.0)
    m = mdl._member("BM")
    n = foundation_segment_count(mdl, m)
    I33 = b * h ** 3 / 12.0
    lc = (4.0 * E_CONC * I33 / (ks * width)) ** 0.25
    seg = L / n
    # segment length obeys the target unless the 40-segment cap intervenes
    assert seg <= min(L / 8.0, lc / 4.0) + 1e-9 or n == 40
    assert 8 <= n <= 40
    # a member without foundation is never split
    plain = _beam_on_foundation(L, b, h, 0.0, 0.0, 100.0)
    assert foundation_segment_count(plain, plain._member("BM")) == 1


# --------------------------------------------------------------------------- #
# 2. gravity load takedown
# --------------------------------------------------------------------------- #
def test_takedown_dead_balance_and_grid_labels():
    """quick_building DEAD: sum of support FZ == total applied dead load, each
    support labelled by its grid intersection, balance_ok True."""
    mdl = quick_building(bays_x=2, bays_y=1, stories=2)
    res = OpenSeesEngine(mdl).run()
    td = res.takedown["DEAD"]
    # hand value: sum of beam UDL * length
    dead = mdl.patterns["DEAD"]
    applied = sum(u.w * mdl._member(u.member_uid).length
                  for u in dead.member_udls
                  if mdl._member(u.member_uid).kind != "column")
    assert td["applied_FZ"] == pytest.approx(applied, rel=1e-12)
    assert td["total_FZ"] == pytest.approx(applied, abs=1e-6)
    assert td["balance_ok"] is True
    # every support labelled by its nearest grid intersection
    labels = {s["grid"] for s in td["supports"]}
    assert "A-1" in labels          # corner column at (0, 0)
    assert "C-2" in labels          # far corner (12, 6)
    for s in td["supports"]:
        assert s["grid"], "every support should carry a grid label"
    # the corner label really is the corner node
    corner = next(s for s in td["supports"] if s["grid"] == "A-1")
    assert (corner["x"], corner["y"]) == (0.0, 0.0)


def test_takedown_combo_superposition():
    """A 1.2D + 1.6L combo takedown FZ equals 1.2*deadFZ + 1.6*liveFZ per
    support (exact linear superposition)."""
    mdl = quick_building(bays_x=2, bays_y=2, stories=2)
    res = OpenSeesEngine(mdl).run()
    td = res.takedown
    assert "1.2D + 1.6L" in td
    dfz = {s["node"]: s["FZ"] for s in td["DEAD"]["supports"]}
    lfz = {s["node"]: s["FZ"] for s in td["LIVE"]["supports"]}
    for s in td["1.2D + 1.6L"]["supports"]:
        expected = 1.2 * dfz[s["node"]] + 1.6 * lfz[s["node"]]
        assert s["FZ"] == pytest.approx(expected, abs=1e-9)
    assert td["1.2D + 1.6L"]["balance_ok"] is True


def test_takedown_present_for_all_static_cases():
    """Takedown is computed for every static case and every additive combo."""
    mdl = quick_building(bays_x=1, bays_y=1, stories=1)
    d = OpenSeesEngine(mdl).run().to_dict()
    assert "takedown" in d
    for name in mdl.cases:
        assert name in d["takedown"]
    for name, cb in mdl.combos.items():
        if cb.combo_type == "add":
            assert name in d["takedown"]
    # shape
    entry = d["takedown"]["DEAD"]
    assert {"supports", "total_FZ", "applied_FZ", "balance_ok"} <= set(entry)
    s0 = entry["supports"][0]
    assert {"node", "grid", "x", "y", "FZ", "FX", "FY"} <= set(s0)


def test_takedown_no_grid_blank_labels():
    """With no grid the takedown supports carry empty grid labels."""
    L, b, h = 24.0, 0.5, 0.5
    mdl = _beam_on_foundation(L, b, h, 20_000.0, 1.0, 100.0)
    assert mdl.grid is None
    td = OpenSeesEngine(mdl).run().takedown["G"]
    assert all(s["grid"] == "" for s in td["supports"])


# --------------------------------------------------------------------------- #
# 3. model round-trip + validation
# --------------------------------------------------------------------------- #
def test_foundation_fields_roundtrip():
    mdl = _beam_on_foundation(10.0, 0.4, 0.4, 15_000.0, 1.2, 50.0)
    d = mdl.to_dict()
    m = next(md for md in d["members"] if md["uid"] == "BM")
    assert m["foundation_ks"] == 15_000.0 and m["foundation_width"] == 1.2
    back = BuildingModel.from_dict(d)
    assert back.to_dict() == d
    assert back._member("BM").foundation_k_line == 15_000.0 * 1.2


def test_foundation_validation():
    mdl = BuildingModel(name="v")
    mdl.add_material(Material("C", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("S", "C", 0.4, 0.4))
    with pytest.raises(ValueError, match="foundation_ks"):
        mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0),
                       foundation_ks=-1.0, foundation_width=1.0)
    with pytest.raises(ValueError, match="foundation_width"):
        mdl.add_member("beam", "S", (0, 0, 0), (5, 0, 0),
                       foundation_ks=1.0, foundation_width=float("nan"))


# --------------------------------------------------------------------------- #
# 4. API
# --------------------------------------------------------------------------- #
def test_api_member_foundation(api_client):
    assert api_client.post("/api/model/quick",
                           json={"bays_x": 1, "bays_y": 1,
                                 "stories": 1}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    beam_uid = next(m["uid"] for m in mdl["members"] if m["kind"] == "beam")
    r = api_client.post("/api/member/foundation",
                        json={"member_uids": [beam_uid], "ks": 25_000.0,
                              "width": 1.5})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    m = next(md for md in body["members"] if md["uid"] == beam_uid)
    assert m["foundation_ks"] == 25_000.0 and m["foundation_width"] == 1.5
    # unknown member -> 400
    bad = api_client.post("/api/member/foundation",
                          json={"member_uids": ["NOPE"], "ks": 1.0,
                                "width": 1.0})
    assert bad.status_code == 400
    # negative -> 400
    bad2 = api_client.post("/api/member/foundation",
                           json={"member_uids": [beam_uid], "ks": -1.0,
                                 "width": 1.0})
    assert bad2.status_code == 400
    # missing width -> 400
    bad3 = api_client.post("/api/member/foundation",
                           json={"member_uids": [beam_uid], "ks": 1.0})
    assert bad3.status_code == 400
    # empty list -> 400
    bad4 = api_client.post("/api/member/foundation",
                           json={"member_uids": [], "ks": 1.0, "width": 1.0})
    assert bad4.status_code == 400


def test_api_model_roundtrips_foundation(api_client):
    assert api_client.post("/api/model/quick",
                           json={"bays_x": 1, "bays_y": 1,
                                 "stories": 1}).status_code == 200
    mdl = api_client.get("/api/model").get_json()
    for m in mdl["members"]:
        if m["kind"] == "beam":
            m["foundation_ks"] = 12_000.0
            m["foundation_width"] = 0.8
    r = api_client.post("/api/model", json=mdl)
    assert r.status_code == 200, r.get_json()
    back = r.get_json()
    beam = next(m for m in back["members"] if m["kind"] == "beam")
    assert beam["foundation_ks"] == 12_000.0
    assert beam["foundation_width"] == 0.8


def test_api_analyze_includes_takedown(api_client):
    assert api_client.post("/api/model/quick",
                           json={"bays_x": 1, "bays_y": 1,
                                 "stories": 1}).status_code == 200
    r = api_client.post("/api/analyze")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert "takedown" in d and "DEAD" in d["takedown"]
    assert d["takedown"]["DEAD"]["balance_ok"] is True
