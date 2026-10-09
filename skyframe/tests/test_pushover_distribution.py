"""Pushover load distribution and control (ETABS Nonlinear Static parity).

Benchmark: an elastic 2-story shear building — quick_building 1x1 bay,
h1 = 4 m, h2 = 3 m, rigid diaphragms, beams made flexurally/axially rigid
(x1e5 / x1e3) and columns axially rigid (x1e4) so each story is an ideal
shear spring k_i = 4 * 12 E I / h_i^3 (I = the column I22, doubled to break
the X/Y period degeneracy; I22 resists X sway).  Explicit story masses
m1 = 20 t, m2 = 10 t live on the diaphragm masters.  No hinge yield
moments are given, so the push is fully elastic and the capacity curve is
a straight line whose slope is the hand value

    K0 = V / d_roof = 1 / sum_i (V_i / k_i),   V_i = sum_{j >= i} f_j,

for the normalised story-force shape f (sum f = 1).  Engine-vs-hand
tolerance 1e-3 (the residual beam/axial flexibility is ~4e-5); exact
identities (shape normalisation, statics, determinism) are checked tight.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

pytest.importorskip("skyframe.engine.opensees_engine")

from skyframe.core.builder import quick_building                # noqa: E402
from skyframe.core.model import (BuildingModel, NodalLoad,      # noqa: E402
                                 StoryForce)
from skyframe.engine.opensees_engine import OpenSeesEngine      # noqa: E402
from skyframe.engine.pushover_distribution import asce7_k       # noqa: E402

E = 25_000_000.0
H1, H2 = 4.0, 3.0
M1, M2 = 20.0, 10.0
HAND_TOL = 1e-3


def _model() -> BuildingModel:
    m = quick_building(name="PO2", bays_x=1, bays_y=1, stories=2,
                       story_height=H2, first_story_height=H1, E=E,
                       column_size=0.5)
    m.sections["BEAM"].I33 *= 1e5
    m.sections["BEAM"].I22 *= 1e5
    m.sections["BEAM"].A *= 1e3
    m.sections["COL"].A *= 1e4
    m.sections["COL"].I22 *= 2.0
    m.story_masses = {"Story1": M1, "Story2": M2}
    return m


def _k(direction: str = "X"):
    m = _model()
    I = m.sections["COL"].I22 if direction == "X" else m.sections["COL"].I33
    return [4 * 12.0 * E * I / h ** 3 for h in (H1, H2)]


def _hand_K0(f, ks) -> float:
    """1 / roof flexibility under story forces f (bottom -> top)."""
    flex = 0.0
    for i, k in enumerate(ks):
        flex += sum(f[i:]) / k
    return 1.0 / flex


def _push(m, name="PO", direction="X", **kw):
    kw.setdefault("target_drift", 0.001)
    kw.setdefault("steps", 4)
    m.add_pushover_case(name, direction, **kw)
    eng = OpenSeesEngine(m)
    return eng, eng.run_pushover(name)


def _K0(po) -> float:
    return po.base_shear[0] / po.roof_disp[0]


def _shape(po):
    sf = po.distribution["story_forces"]
    return [sf["Story1"], sf["Story2"]]


def _hand_modes(ks):
    k1, k2 = ks
    K = np.array([[k1 + k2, -k2], [-k2, k2]])
    Mi = np.diag([1 / math.sqrt(M1), 1 / math.sqrt(M2)])
    w2, v = np.linalg.eigh(Mi @ K @ Mi)
    phi = Mi @ v[:, 0]
    return w2, phi


# --------------------------------------------------------------- defaults
def test_default_byte_identical_to_explicit_roof_point():
    """Defaults == explicit roof_point/displacement_control/roof control
    story (the same node): every pre-feature result list identical."""
    _, a = _push(_model(), default_My=50.0, target_drift=0.01, steps=8)
    _, b = _push(_model(), default_My=50.0, target_drift=0.01, steps=8,
                 load_distribution="roof_point", control_story="Story2",
                 control_mode="displacement_control", control_dof="UX")
    da, db = a.to_dict(), b.to_dict()
    for key in ("roof_disp", "base_shear", "roof_drift", "hinge_rotations",
                "warnings", "hinges"):
        assert da[key] == db[key]
    assert da["distribution"]["type"] == "roof_point"
    assert da["distribution"]["story_forces"] == {"Story2": 1.0}


def test_default_case_serializes_without_new_keys():
    m = _model()
    po = m.add_pushover_case("PO", "X")
    assert set(po.to_dict()) == {
        "name", "direction", "gravity", "target_drift", "steps", "hinges",
        "My", "default_My", "hardening", "hinge_params", "geometric"}


def test_roof_point_initial_stiffness_hand():
    _, po = _push(_model())
    assert _K0(po) == pytest.approx(_hand_K0([0.0, 1.0], _k()), rel=HAND_TOL)


# ----------------------------------------------------------- uniform_accel
def test_uniform_accel_shape_proportional_to_masses():
    _, po = _push(_model(), load_distribution="uniform_accel")
    f = _shape(po)
    assert f == pytest.approx([M1 / (M1 + M2), M2 / (M1 + M2)], abs=1e-14)
    assert po.distribution["reference_base_shear"] == pytest.approx(
        1.0, abs=1e-14)


def test_uniform_accel_initial_stiffness_hand():
    _, po = _push(_model(), load_distribution="uniform_accel")
    assert _K0(po) == pytest.approx(_hand_K0(_shape(po), _k()), rel=HAND_TOL)
    assert _K0(po) == pytest.approx(
        _hand_K0([M1 / 30.0, M2 / 30.0], _k()), rel=HAND_TOL)


# -------------------------------------------------------------- triangular
@pytest.mark.parametrize("k", [1.0, 2.0])
def test_triangular_matches_asce7_cvx(k):
    """C_vx = w_x h_x^k / sum w_i h_i^k (ASCE 7-16 Eq. 12.8-12)."""
    _, po = _push(_model(), load_distribution="triangular", k=k)
    h = [H1, H1 + H2]
    w = [M1, M2]
    den = sum(wi * hi ** k for wi, hi in zip(w, h))
    cvx = [wi * hi ** k / den for wi, hi in zip(w, h)]
    assert _shape(po) == pytest.approx(cvx, rel=1e-12)
    assert po.distribution["params"]["k"] == k
    assert _K0(po) == pytest.approx(_hand_K0(cvx, _k()), rel=HAND_TOL)


def test_triangular_auto_k_from_period():
    eng, po = _push(_model(), load_distribution="triangular")
    p = po.distribution["params"]
    T = p["period"]
    # the X-dominant engine period (mode 2 here; Y sway is mode 1)
    md = eng.run_modal()
    assert T == pytest.approx(md.periods[p["mode_number"] - 1], rel=1e-9)
    assert md.participation[p["mode_number"] - 1]["ux"] > 0.9
    assert p["k"] == asce7_k(T) == 1.0          # short period -> k = 1


def test_asce7_k_values():
    assert asce7_k(0.3) == 1.0
    assert asce7_k(0.5) == 1.0
    assert asce7_k(1.5) == pytest.approx(1.5)
    assert asce7_k(2.5) == 2.0
    assert asce7_k(4.0) == 2.0


# ------------------------------------------------------------------- mode
def test_mode_shape_matches_model_eigen_m_phi():
    """f_s = m_s phi_s / sum(m phi) from the engine's own run_modal."""
    eng, po = _push(_model(), load_distribution="mode")
    mode = po.distribution["params"]["mode_number"]
    md = eng.run_modal()
    asm = eng._asm                                # elastic build of run_modal
    phi = [md.shapes[mode][asm.masters[s]][0] for s in ("Story1", "Story2")]
    mphi = [M1 * phi[0], M2 * phi[1]]
    tot = sum(mphi)
    f = _shape(po)
    assert f == pytest.approx([v / tot for v in mphi], rel=1e-8)
    assert sum(f) == pytest.approx(1.0, abs=1e-14)
    assert all(v > 0 for v in f)                  # first mode: same sign
    assert md.participation[mode - 1]["ux"] > 0.9  # X-dominant mode picked
    assert po.distribution["params"]["period"] == pytest.approx(
        md.periods[mode - 1], rel=1e-9)


def test_mode_shape_matches_hand_two_dof():
    _, po = _push(_model(), load_distribution="mode")
    w2, phi = _hand_modes(_k())
    mphi = np.array([M1, M2]) * phi
    hand = mphi / mphi.sum()
    assert _shape(po) == pytest.approx(list(hand), rel=HAND_TOL)
    T_hand = 2 * math.pi / math.sqrt(w2[0])
    assert po.distribution["params"]["period"] == pytest.approx(
        T_hand, rel=HAND_TOL)


def test_mode_initial_stiffness_hand():
    _, po = _push(_model(), load_distribution="mode")
    _, phi = _hand_modes(_k())
    mphi = np.array([M1, M2]) * phi
    assert _K0(po) == pytest.approx(
        _hand_K0(list(mphi / mphi.sum()), _k()), rel=HAND_TOL)


def test_mode_y_direction_and_explicit_mode_number():
    _, po = _push(_model(), direction="Y", load_distribution="mode",
                  mode_number=1)
    assert po.distribution["params"]["mode_number"] == 1
    assert po.control["dof"] == "UY"
    f = _shape(po)
    _, phi = _hand_modes(_k("Y"))
    mphi = np.array([M1, M2]) * phi
    assert f == pytest.approx(list(mphi / mphi.sum()), rel=HAND_TOL)
    assert _K0(po) == pytest.approx(_hand_K0(f, _k("Y")), rel=HAND_TOL)


# ---------------------------------------------------------------- pattern
def _with_pattern(m):
    p = m.pattern("PUSHPAT")
    p.story_forces.append(StoryForce("Story1", fx=30.0))
    p.story_forces.append(StoryForce("Story2", fx=70.0))
    p.nodal_loads.append(NodalLoad((0.0, 0.0, H1 + H2), fx=10.0))
    m.add_case("PAT", {"PUSHPAT": 1.0})
    return m


def test_pattern_reproduces_linear_static_at_load_level():
    m = _with_pattern(_model())
    eng = OpenSeesEngine(m)
    st = eng.run_static("PAT")
    u_roof = st.node_disp[eng._asm.masters["Story2"]][0]
    V_static = -st.base["FX"]
    assert V_static == pytest.approx(110.0, rel=1e-9)
    _, po = _push(m, load_distribution="pattern", pattern="PUSHPAT")
    assert po.distribution["reference_base_shear"] == pytest.approx(
        110.0, rel=1e-9)
    for d, V in zip(po.roof_disp, po.base_shear):
        lam = V / V_static
        assert d == pytest.approx(lam * u_roof, rel=1e-6)
    assert _K0(po) == pytest.approx(
        _hand_K0([30 / 110, 80 / 110], _k()), rel=HAND_TOL)


def test_pattern_story_shape_normalised():
    m = _with_pattern(_model())
    _, po = _push(m, load_distribution="pattern", pattern="PUSHPAT")
    assert _shape(po) == pytest.approx([30 / 110, 80 / 110], rel=1e-12)
    assert po.distribution["params"] == {"pattern": "PUSHPAT"}


# ----------------------------------------------------------- control modes
def test_load_control_applies_full_pattern_in_steps():
    m = _with_pattern(_model())
    eng = OpenSeesEngine(m)
    st = eng.run_static("PAT")
    u_roof = st.node_disp[eng._asm.masters["Story2"]][0]
    _, po = _push(m, load_distribution="pattern", pattern="PUSHPAT",
                  control_mode="load_control", target_load=2.0, steps=4)
    assert po.base_shear == pytest.approx([55.0, 110.0, 165.0, 220.0],
                                          rel=1e-12)
    assert po.roof_disp[-1] == pytest.approx(2.0 * u_roof, rel=1e-6)
    assert po.control["mode"] == "load_control"


def test_load_control_shape_target_is_base_shear():
    _, po = _push(_model(), load_distribution="triangular", k=1.0,
                  control_mode="load_control", target_load=500.0, steps=5)
    assert po.base_shear[-1] == pytest.approx(500.0, rel=1e-12)
    _, pd = _push(_model(), load_distribution="triangular", k=1.0)
    assert _K0(po) == pytest.approx(_K0(pd), rel=1e-6)


def test_control_story_monitors_lower_story():
    """Uniform push, monitored at Story1: V / d1 = k1 (story-1 shear = V)."""
    _, po = _push(_model(), load_distribution="uniform_accel",
                  control_story="Story1", target_drift=0.001, steps=2)
    assert _K0(po) == pytest.approx(_k()[0], rel=HAND_TOL)
    assert po.control["height"] == pytest.approx(H1)
    assert po.roof_disp[-1] == pytest.approx(0.001 * H1, rel=1e-9)
    assert po.roof_drift[-1] == pytest.approx(0.001, rel=1e-9)


def test_control_point_on_diaphragm_slave_and_target_disp():
    """A column-top joint (diaphragm slave) as monitored joint; the master
    is driven, the joint's own displacement recorded (symmetric push: no
    twist, so they coincide).  target_disp overrides target_drift."""
    _, po = _push(_model(), load_distribution="uniform_accel",
                  control_point=[0.0, 0.0, H1 + H2], target_disp=0.003)
    assert po.roof_disp[-1] == pytest.approx(0.003, rel=1e-9)
    _, ref = _push(_model(), load_distribution="uniform_accel")
    assert _K0(po) == pytest.approx(_K0(ref), rel=1e-9)


# --------------------------------------------------------------- start_from
def test_start_from_static_case_equals_gravity_dict():
    m1 = _model()
    m1.add_case("GRAV", {"DEAD": 1.0, "LIVE": 0.5})
    _, a = _push(m1, start_from="GRAV", default_My=80.0, target_drift=0.01,
                 steps=6)
    _, b = _push(_model(), gravity={"DEAD": 1.0, "LIVE": 0.5},
                 default_My=80.0, target_drift=0.01, steps=6)
    assert a.roof_disp == b.roof_disp
    assert a.base_shear == b.base_shear
    assert a.hinge_rotations == b.hinge_rotations
    assert a.control["start_from"] == "GRAV"


# --------------------------------------------------------- model round-trip
def test_model_round_trip_preserves_fields():
    m = _with_pattern(_model())
    m.add_case("GRAV", {"DEAD": 1.0})
    m.add_pushover_case("A", "X", load_distribution="mode", mode_number=2,
                        control_point=[0.0, 0.0, 7.0], control_dof="UX",
                        target_disp=0.05, start_from="GRAV")
    m.add_pushover_case("B", "Y", load_distribution="pattern",
                        pattern="PUSHPAT", control_mode="load_control",
                        target_load=3.0, control_story="Story1")
    m.add_pushover_case("C", "X", load_distribution="triangular", k=1.5)
    text = json.dumps(m.to_dict())
    m2 = BuildingModel.from_dict(json.loads(text))
    for n in ("A", "B", "C"):
        assert m2.pushover_cases[n] == m.pushover_cases[n]
        assert m2.pushover_cases[n].to_dict() == m.pushover_cases[n].to_dict()
    assert m2.pushover_cases["A"].control_point == [0.0, 0.0, 7.0]
    m2.validate()


def test_results_json_shape():
    _, po = _push(_model(), load_distribution="uniform_accel")
    d = json.loads(json.dumps(po.to_dict()))
    cc = d["capacity_curve"]
    assert set(cc) == {"node", "dof", "mode", "height", "start_from",
                       "disp", "base_shear"}
    assert cc["disp"] == d["roof_disp"] and cc["base_shear"] == d["base_shear"]
    assert set(d["distribution"]) == {"type", "story_forces",
                                      "normalization",
                                      "reference_base_shear", "params"}


# -------------------------------------------------------------- validation
@pytest.mark.parametrize("kw, msg", [
    ({"load_distribution": "parabolic"}, "load_distribution"),
    ({"load_distribution": "pattern"}, "pattern"),
    ({"load_distribution": "pattern", "pattern": "NOPE"}, "unknown load"),
    ({"mode_number": 0}, "mode_number"),
    ({"k": -1.0}, "k must"),
    ({"control_story": "Story9"}, "control_story"),
    ({"control_story": "Story1", "control_point": [0, 0, 4]}, "not both"),
    ({"control_point": [0, 0]}, "control_point"),
    ({"control_dof": "UZ"}, "control_dof"),
    ({"target_disp": 0.0}, "target_disp"),
    ({"control_mode": "arc_length"}, "control_mode"),
    ({"target_load": float("nan")}, "target_load"),
    ({"start_from": "NOPE"}, "start_from"),
    ({"start_from": "GRAV", "gravity": {"DEAD": 1.0}}, "not both"),
])
def test_validation_errors(kw, msg):
    m = _model()
    m.add_case("GRAV", {"DEAD": 1.0})
    with pytest.raises(ValueError, match=msg):
        m.add_pushover_case("BAD", "X", **kw)


def test_unknown_option_is_type_error():
    with pytest.raises(TypeError):
        _model().add_pushover_case("BAD", "X", load_shape="mode")
