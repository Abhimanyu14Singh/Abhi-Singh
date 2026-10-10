"""Cross-feature integration checks for the v1.13 wave.

Features built in parallel are pinned here where they interact:
Set Load Cases to Run x steady-state / PSD load cases.
"""

import json

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.model import MODAL_CASE  # noqa: E402
from test_frequency import _model_with_cases  # noqa: E402


def test_frequency_cases_are_listed_case_kinds():
    kinds = _model_with_cases().case_kinds()
    assert kinds["SS"] == "steady_state"
    assert kinds["PSD"] == "psd"


def test_frequency_cases_run_by_default():
    res = OpenSeesEngine(_model_with_cases()).run().to_dict()
    assert set(res["steady_state"]) == {"SS"}
    assert set(res["psd"]) == {"PSD"}
    assert res["case_status"]["SS"] == "finished"
    assert res["case_status"]["PSD"] == "finished"


def test_not_run_frequency_cases_are_skipped():
    mdl = _model_with_cases()
    mdl.cases_not_run = ["SS", "PSD"]
    mdl.validate()                      # names are accepted, not "unknown"
    res = OpenSeesEngine(mdl).run().to_dict()
    assert "steady_state" not in res and "psd" not in res
    assert res["case_status"]["SS"] == "not_run"
    assert res["case_status"]["PSD"] == "not_run"


def test_frequency_case_runs_modal_as_dependency():
    mdl = _model_with_cases()
    mdl.cases_not_run = [MODAL_CASE, "SS"]
    res = OpenSeesEngine(mdl).run().to_dict()
    assert res["case_status"]["PSD"] == "finished"
    assert res["case_status"][MODAL_CASE] == "run_as_dependency"
    assert res["case_status"]["SS"] == "not_run"


def test_skipping_one_frequency_case_leaves_the_other_identical():
    full = OpenSeesEngine(_model_with_cases()).run().to_dict()
    mdl = _model_with_cases()
    mdl.cases_not_run = ["SS"]
    part = OpenSeesEngine(mdl).run().to_dict()
    assert part["psd"]["PSD"]["rms"] == full["psd"]["PSD"]["rms"]


# --------------------------------------------------------------------------- #
# Modal load participation is on demand (it costs 3 + n_patterns solves)
# --------------------------------------------------------------------------- #
def test_plain_run_omits_load_participation():
    res = OpenSeesEngine(_model_with_cases()).run().to_dict()
    assert "load_participation" not in res["modal"]


def test_on_demand_load_participation_equals_opt_in_run():
    mdl = _model_with_cases()
    via_run = OpenSeesEngine(mdl).run(load_participation=True).to_dict()
    direct = OpenSeesEngine(mdl).run_load_participation()
    assert direct == via_run["modal"]["load_participation"]
    # SDOF with its one mode: every massed load is fully captured
    assert direct["acceleration"]["UX"]["dynamic"] == pytest.approx(100.0)


def test_load_participation_endpoint():
    from skyframe.api.server import create_app
    client = create_app().test_client()
    r = client.post("/api/model", json=_model_with_cases().to_dict())
    assert r.status_code == 200
    r = client.post("/api/analyze/load_participation")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"acceleration", "patterns"}
    assert set(body["acceleration"]) == {"UX", "UY", "UZ"}


# --------------------------------------------------------------------------- #
# Features built in parallel that meet in one model
# --------------------------------------------------------------------------- #
import math  # noqa: E402

from skyframe.core.builder import quick_building  # noqa: E402
from skyframe.core.model import (BuildingModel, FrameSection,  # noqa: E402
                                 Material, MemberLoad, NodalLoad,
                                 PointSupport)

_E, _NU = 30_000_000.0, 0.2


def _deep_cantilever(shear: bool, load) -> BuildingModel:
    """2 m cantilever along X, 0.3 x 1.0 m section, fixed at the origin."""
    m = BuildingModel(name="t")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=_E, nu=_NU, unit_weight=25.0))
    m.set_stories([3.0])
    sec = FrameSection.rectangular("D", "C", 0.3, 1.0)
    sec.shear_deformation = shear
    m.add_section(sec)
    m.add_member("beam", "D", (0, 0, 0), (2.0, 0, 0), uid="B1")
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    load(m.pattern("P"))
    m.add_case("P", {"P": 1.0})
    return m


def _tip_uz(m: BuildingModel) -> float:
    eng = OpenSeesEngine(m)
    res = eng.run_static("P")
    return res.node_disp[eng._find_node(eng._asm, (2.0, 0, 0))][2]


def test_span_moment_on_timoshenko_member_matches_closed_form():
    """Loads wave x shear-deformation wave.  A concentrated span moment on
    a cantilever produces no shear force, so the Timoshenko member must give
    exactly the Euler-Bernoulli answer: tip deflection
    M a^2/(2EI) + (M a / EI)(L - a)."""
    M, L, a = 50.0, 2.0, 1.0
    I33 = 0.3 * 1.0 ** 3 / 12.0

    def load(p):
        p.member_loads.append(MemberLoad("B1", kind="moment", w=M, a=a / L,
                                         direction="global_y"))

    euler = _tip_uz(_deep_cantilever(False, load))
    timo = _tip_uz(_deep_cantilever(True, load))
    hand = M * a * a / (2 * _E * I33) + M * a / (_E * I33) * (L - a)
    assert abs(euler) == pytest.approx(hand, rel=1e-9)
    assert timo == pytest.approx(euler, rel=1e-9)


def test_active_dof_xz_plane_with_timoshenko_member():
    """Active-DOF wave x shear-deformation wave: an XZ-plane solve of an
    in-plane Timoshenko cantilever equals the full 3D answer
    P L^3/(3EI) + P L/(G As)."""
    P, L = 100.0, 2.0

    def load(p):
        p.nodal_loads.append(NodalLoad((L, 0, 0), fz=-P))

    full = _tip_uz(_deep_cantilever(True, load))
    planar = _deep_cantilever(True, load)
    planar.active_dof = ["UX", "UZ", "RY"]
    xz = _tip_uz(planar)
    I33 = 0.3 * 1.0 ** 3 / 12.0
    G = _E / (2 * (1 + _NU))
    hand = P * L ** 3 / (3 * _E * I33) + P * L / (G * 5.0 / 6.0 * 0.3)
    assert xz == pytest.approx(full, rel=1e-12)
    assert abs(xz) == pytest.approx(hand, rel=1e-9)


def test_weight_modifier_scales_only_the_self_mass_part():
    """Mass-source wave x modifiers wave: mod_weight scales the self-weight
    share of the story mass and leaves pattern mass alone."""
    def building():
        m = quick_building(bays_x=1, bays_y=1, stories=2)
        m.patterns["DEAD"].self_weight_factor = 1.0   # DEAD is in the mass source
        return m

    base = building()
    self_full = base.compute_story_masses(self_mass=True, patterns=False)
    pat_full = base.compute_story_masses(self_mass=False, patterns=True)
    half = building()
    for sec in half.sections.values():
        sec.mod_weight = 0.5
    self_half = half.compute_story_masses(self_mass=True, patterns=False)
    pat_half = half.compute_story_masses(self_mass=False, patterns=True)
    for s in self_full:
        assert self_full[s] > 0
        assert self_half[s] == pytest.approx(0.5 * self_full[s], rel=1e-12)
        assert pat_half[s] == pytest.approx(pat_full[s], rel=1e-12)


def test_mass_modifier_in_element_self_mass_mode_scales_period():
    """Mass-density self mass with every frame at mod_mass = 0.5 halves all
    mass uniformly, so every period drops by exactly sqrt(2)."""
    def periods(mod):
        m = quick_building(bays_x=1, bays_y=1, stories=2)
        m.mass_source_mode = "element_self_mass"
        m.story_masses = {}
        for sec in m.sections.values():
            sec.mod_mass = mod
        return OpenSeesEngine(m).run_modal().periods

    t1, th = periods(1.0), periods(0.5)
    assert th[0] == pytest.approx(t1[0] / math.sqrt(2.0), rel=1e-6)


def test_results_tables_skip_not_run_cases():
    """Run-control wave x results-tables wave (through the API)."""
    from skyframe.api.server import create_app
    client = create_app().test_client()
    mdl = quick_building(bays_x=1, bays_y=1, stories=2)
    mdl.cases_not_run = ["EQY"]
    assert client.post("/api/model", json=mdl.to_dict()).status_code == 200
    res = client.post("/api/analyze").get_json()
    assert res["case_status"]["EQY"] == "not_run"
    r = client.post("/api/tables/story_drifts")
    assert r.status_code == 200
    rows = r.get_json()["rows"]
    assert rows                                   # other cases present
    assert not any("EQY" in json.dumps(row) for row in rows)


def test_equilibrium_table_understands_new_load_types():
    """Results-tables wave x loads wave x modifiers wave: span moments,
    directional / joint-pattern area loads and weight modifiers all keep
    the load-pattern equilibrium error at ~0, and a settlement-only pattern
    reports no (meaningless) percentage."""
    from skyframe.core import tables
    from skyframe.core.loads_ext import GroundDisplacement
    from skyframe.core.model import AreaLoad
    from test_tables import _equilibrium_model

    mdl = _equilibrium_model()
    h = 3.5
    for sec in mdl.sections.values():
        sec.mod_weight = 0.7
    mdl.shell_sections["WL"].weight = 0.5
    new = mdl.pattern("NEW", "other")
    new.member_loads.append(MemberLoad("B1", kind="moment", w=12.0, a=0.3,
                                       direction="local_z"))
    new.area_loads.append(AreaLoad(
        "W1", 10.0, direction="global_x",
        joint_pattern={"type": "linear", "c": -1.0, "d": h}))
    mdl.add_case("NEW", {"NEW": 1.0})
    mdl.pattern("SET").ground_displacements.append(
        GroundDisplacement((0.0, 0.0, 0.0), uz=-0.005))
    mdl.add_case("SET", {"SET": 1.0})

    eng = OpenSeesEngine(mdl)
    d = eng.run().to_dict()
    rows = tables.compute_table("load_pattern_summary", d, mdl,
                                tables.engine_context(eng))["rows"]
    by = {r["pattern"]: r for r in rows}
    # hydrostatic resultant gamma h^2 b / 2 along +X (wall width b = 4 m)
    assert by["NEW"]["FX"] == pytest.approx(10.0 * h ** 2 * 4.0 / 2.0,
                                            rel=1e-9)
    assert by["NEW"]["FZ"] == pytest.approx(0.0, abs=1e-9)  # couple: no force
    for name in ("SW", "DL", "LAT", "NEW"):
        assert by[name]["error_pct"] < 1e-6, by[name]
    assert by["SET"]["error_pct"] is None


def test_span_moment_on_indeterminate_timoshenko_beam_matches_split_model():
    """Fixed-fixed deep Timoshenko beam, couple at 0.3 L: end reactions from
    the span-load path equal an independent model split at the load point
    with the couple applied as a joint moment (exact nodal path)."""
    L, a, M = 4.0, 1.2, 40.0

    def beam(split: bool) -> BuildingModel:
        m = BuildingModel(name="t")
        m.rigid_diaphragms = False
        m.add_material(Material("C", E=_E, nu=_NU, unit_weight=25.0))
        m.set_stories([3.0])
        sec = FrameSection.rectangular("D", "C", 0.3, 1.0)
        sec.shear_deformation = True
        m.add_section(sec)
        m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        m.supports.append(PointSupport((L, 0, 0), (1, 1, 1, 1, 1, 1)))
        p = m.pattern("P")
        if split:
            m.add_member("beam", "D", (0, 0, 0), (a, 0, 0), uid="B1")
            m.add_member("beam", "D", (a, 0, 0), (L, 0, 0), uid="B2")
            p.nodal_loads.append(NodalLoad((a, 0, 0), my=M))
        else:
            m.add_member("beam", "D", (0, 0, 0), (L, 0, 0), uid="B1")
            p.member_loads.append(MemberLoad("B1", kind="moment", w=M,
                                             a=a / L, direction="global_y"))
        m.add_case("P", {"P": 1.0})
        return m

    def reactions(m):
        eng = OpenSeesEngine(m)
        res = eng.run_static("P")
        return [res.reactions[eng._find_node(eng._asm, pt)]
                for pt in ((0, 0, 0), (L, 0, 0))]

    span, split = reactions(beam(False)), reactions(beam(True))
    scale = max(abs(v) for r in split for v in r)
    for rs, rp in zip(span, split):
        for vs, vp in zip(rs, rp):
            assert vs == pytest.approx(vp, abs=1e-9 * scale)
    # shear flexibility matters here: Euler end shear 6 M a b / L^3 differs
    b = L - a
    euler_V = 6.0 * M * a * b / L ** 3
    assert abs(abs(split[0][2]) - euler_V) > 1e-3 * euler_V


# --------------------------------------------------------------------------- #
# v1.14: model-wide P-Delta x member types from other waves
# --------------------------------------------------------------------------- #
def _pd_identity(mutate):
    """Single-element cantilever: the iterative P-Delta string stiffness is
    exactly N/L, so 1/u_pd = 1/u_lin - P/L whatever the element formulation,
    provided the axial force N is read correctly from that element."""
    from test_pdelta_options import EI, _column, _tip
    L = 3.0
    P = 0.4 * 3.0 * EI / L ** 2

    def tip_u(method):
        mdl = _column(L=L, P=P, method=method)
        mutate(mdl)
        eng = OpenSeesEngine(mdl)
        return eng.run_static("H").node_disp[_tip(eng, L)][0]

    lin, pd = tip_u("none"), tip_u("iterative_loads")
    assert 1.0 / pd == pytest.approx(1.0 / lin - P / L, rel=1e-6)
    return lin


def test_pdelta_iterative_on_timoshenko_member():
    def shear(mdl):
        mdl.sections["COL"].shear_deformation = True
    lin_t = _pd_identity(shear)
    lin_e = _pd_identity(lambda mdl: None)
    assert abs(lin_t) > abs(lin_e)          # shear flexibility is really on


def test_pdelta_iterative_on_offset_member():
    def offset(mdl):
        mdl.members[0].cardinal_point = 8
    _pd_identity(offset)


def test_pdelta_iterative_with_xz_active_dof():
    def planar(mdl):
        mdl.active_dof = ["UX", "UZ", "RY"]
    _pd_identity(planar)


# --------------------------------------------------------------------------- #
# v1.15: frame additional mass x frame auto-mesh
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["lumped", "distributed"])
def test_additional_mass_total_survives_auto_mesh_split(mode):
    """A beam carrying additional mass m per length, split into segments by
    frame auto-mesh, must still contribute exactly m * L of translational
    mass, the same as the unsplit beam."""
    def total_mass(split):
        mdl = BuildingModel(name="t")
        mdl.rigid_diaphragms = False
        mdl.add_material(Material("C", E=_E, nu=_NU, unit_weight=0.0))
        mdl.set_stories([3.0])
        mdl.add_section(FrameSection.rectangular("B", "C", 0.3, 0.5))
        mdl.add_member("beam", "B", (0, 0, 3), (6, 0, 3), uid="B1")
        mdl.supports.append(PointSupport((0, 0, 3), (1, 1, 1, 1, 1, 1)))
        mdl.supports.append(PointSupport((6, 0, 3), (1, 1, 1, 1, 1, 1)))
        m = mdl.members[0]
        m.additional_mass = 0.8
        m.additional_mass_mode = mode
        if split:
            m.auto_mesh = {"at_intermediate_joints": False,
                           "at_intersections": False,
                           "max_length": 1.0, "min_segments": None}
        mdl.validate()
        eng = OpenSeesEngine(mdl)
        eng._build()
        return sum(v for (t, dof), v in eng._asm.mass_map.items() if dof == 1)

    whole, split = total_mass(False), total_mass(True)
    assert whole == pytest.approx(0.8 * 6.0, rel=1e-12)
    assert split == pytest.approx(whole, rel=1e-12)


# --------------------------------------------------------------------------- #
# A rectangle given only by b x h (e.g. a new Section Manager row) loads
# with the exact rectangular properties instead of failing on missing "A".
# --------------------------------------------------------------------------- #
def test_section_with_only_b_h_derives_rectangular_properties():
    d = quick_building(bays_x=1, bays_y=1, stories=1).to_dict()
    mat = next(iter(d["materials"]))
    d["sections"]["R"] = {"name": "R", "material": mat, "b": 0.3, "h": 0.6}
    m = BuildingModel.from_dict(d)
    ref = FrameSection.rectangular("R", mat, 0.3, 0.6)
    s = m.sections["R"]
    for k in ("A", "I33", "I22", "J", "b", "h"):
        assert getattr(s, k) == pytest.approx(getattr(ref, k), rel=1e-15)
