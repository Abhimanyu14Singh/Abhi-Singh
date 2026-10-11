"""Mesh-sensitivity study + accuracy advisor (CONTRACT "Mesh sensitivity
study and accuracy advisor").

Closed-form validation:

* hard simply-supported square plate under uniform q (Timoshenko &
  Woinowsky-Krieger, Table 8): centre deflection w = 0.00406 q a^4 / D and
  max moment M = 0.0479 q a^2 (nu = 0.3); the Richardson estimate of the
  1 / 2 / 4 refinement sequence is within 1 % of both, the sequence is
  monotone and the level-to-level change shrinks;
* SS plate fundamental period T11 = 2 pi / (2 pi^2 / a^2 sqrt(D / rho t));
* in-plane cantilever wall (nu = 0, rigid top diaphragm): tip deflection
  converges monotonically toward P H^3 / (3 E I) + P H / (kappa G A),
  kappa = 5/6, Richardson within 1 %;
* Richardson extrapolation exact on f = C + A h^p sequences.
"""

import json
import math
import warnings

import pytest

from skyframe.core import mesh_study as MS
from skyframe.core.accuracy_advisor import (DEFAULTS, accuracy_advice,
                                            element_metrics, region_span)
from skyframe.core.checks import load_model_lenient
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 LineSpring, Material, MemberLoad, Opening,
                                 PointSupport, ShellSection, StoryForce)
from skyframe.engine.opensees_engine import OpenSeesEngine

warnings.simplefilter("ignore")

E_C = 25.0e6


def _js(m):
    return json.dumps(m.to_dict(), sort_keys=True)


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def ss_plate(mesh=1.0, a=4.0, t=0.05, nu=0.3, q=10.0, Z=3.0, k=1e10,
             mods=None, shell_type="shell"):
    """Hard SS plate: grounded vertical line springs (penalty k) on every
    edge -- they discretise over whatever mesh nodes lie on the edges, so
    the boundary condition holds on every refinement level."""
    m = BuildingModel(name="ssplate")
    m.num_modes = 0
    m.add_material(Material("C", E=E_C, nu=nu, unit_weight=0.0))
    m.add_shell_section(ShellSection("SL", "C", t, shell_type=shell_type,
                                     **(mods or {})))
    m.set_stories([Z])
    m.add_shell("slab", "shell", "SL",
                [(0, 0, Z), (a, 0, Z), (a, a, Z), (0, a, Z)],
                mesh_size=mesh, story="Story1", uid="S1")
    m.pattern("Q").area_loads.append(AreaLoad("S1", q))
    for p1, p2 in (((0, 0), (a, 0)), ((a, 0), (a, a)), ((a, a), (0, a)),
                   ((0, a), (0, 0))):
        m.line_springs.append(LineSpring((*p1, Z), (*p2, Z), kz=k))
    m.supports.append(PointSupport((0, 0, Z), (1, 1, 0, 0, 0, 1)))
    m.supports.append(PointSupport((a, 0, Z), (0, 1, 0, 0, 0, 0)))
    m.add_case("Q", {"Q": 1.0})
    D = E_C * t ** 3 / (12 * (1 - nu ** 2))
    return m, D


def cantilever_wall(mesh=1.0, H=6.0, B=2.0, t=0.2, P=100.0):
    m = BuildingModel(name="wall")
    m.num_modes = 0
    m.add_material(Material("C", E=E_C, nu=0.0, unit_weight=0.0))
    m.add_shell_section(ShellSection("W", "C", t))
    m.set_stories([H])
    m.add_shell("wall", "shell", "W",
                [(0, 0, 0), (B, 0, 0), (B, 0, H), (0, 0, H)],
                mesh_size=mesh, story="Story1", uid="W1")
    m.pattern("P").story_forces.append(StoryForce("Story1", fx=P))
    m.add_case("P", {"P": 1.0})
    I, A, G = t * B ** 3 / 12.0, t * B, E_C / 2.0
    return m, P * H ** 3 / (3 * E_C * I) + P * H / (5.0 / 6.0 * G * A)


def portal_with_slab():
    """2 columns + edge beam + slab; beam carries a mid-span point load."""
    m = BuildingModel(name="portal")
    m.num_modes = 0
    m.add_material(Material("C", E=E_C, nu=0.2, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("COL", "C", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BM", "C", 0.3, 0.6))
    m.add_shell_section(ShellSection("SL", "C", 0.2))
    m.set_stories([3.0])
    for k, (x, y) in enumerate(((0, 0), (6, 0), (6, 4), (0, 4))):
        m.add_member("column", "COL", (x, y, 0), (x, y, 3), story="Story1",
                     uid=f"C{k}")
    m.add_member("beam", "BM", (0, 0, 3), (6, 0, 3), story="Story1",
                 uid="B1")
    m.add_member("beam", "BM", (0, 4, 3), (6, 4, 3), story="Story1",
                 uid="B2")
    m.add_shell("slab", "shell", "SL",
                [(0, 0, 3), (6, 0, 3), (6, 4, 3), (0, 4, 3)],
                mesh_size=1.0, story="Story1", uid="S1")
    p = m.pattern("L")
    p.member_loads.append(MemberLoad("B1", kind="point", w=50.0, a=0.5))
    p.area_loads.append(AreaLoad("S1", 5.0))
    m.add_case("L", {"L": 1.0})
    return m


def _row(res, case, key):
    rows = [r for r in res["rows"] if r["case"] == case and r["key"] == key]
    assert len(rows) == 1, (case, key, [r["key"] for r in res["rows"]])
    return rows[0]


# --------------------------------------------------------------------------- #
# Richardson / rows
# --------------------------------------------------------------------------- #
def test_richardson_exact_on_power_law_sequences():
    for p, C, A in ((2.0, 1.0, 0.5), (1.5, 3.0, -2.0), (1.0, -4.0, 7.0)):
        f = lambda lv: C + A * (1.0 / lv) ** p   # noqa: E731
        r = MS.richardson([1, 2, 4], [f(1), f(2), f(4)])
        assert r["order"] == pytest.approx(p, rel=1e-9)
        assert r["estimate"] == pytest.approx(C, rel=1e-9, abs=1e-12)
        assert r["order_assumed"] is False
        # unequal refinement ratios (generalised order equation)
        r = MS.richardson([1, 2, 3], [f(1), f(2), f(3)])
        assert r["order"] == pytest.approx(p, rel=1e-7)
        assert r["estimate"] == pytest.approx(C, rel=1e-7, abs=1e-12)


def test_richardson_two_levels_and_oscillation_assume_order_2():
    r = MS.richardson([1, 2], [1.0 + 0.5, 1.0 + 0.5 / 4])
    assert r["order_assumed"] and r["order"] == MS.ASSUMED_ORDER
    assert r["estimate"] == pytest.approx(1.0, rel=1e-12)
    r = MS.richardson([1, 2, 4], [1.0, 2.0, 1.5])         # oscillating
    assert r["order_assumed"]
    assert r["estimate"] == pytest.approx(1.5 + (1.5 - 2.0) / 3.0)
    r = MS.richardson([1, 2, 4], [1.0, 2.0, 2.0])          # flat
    assert r["estimate"] == 2.0 and r["order"] is None


def test_make_row_relative_change_and_verdicts():
    row = MS.make_row("Q", "roof_disp", "roof_disp:2", "x", "length",
                      [1, 2, 4], [0.8, 0.95, 0.9875], 5.0)
    assert row["rel_change_pct"][0] is None
    assert row["rel_change_pct"][1] == pytest.approx(0.15 / 0.95 * 100)
    assert row["rel_change_pct"][2] == pytest.approx(0.0375 / 0.9875 * 100)
    assert row["richardson"] == pytest.approx(1.0)
    assert row["order"] == pytest.approx(2.0)
    assert row["est_error_pct"] == pytest.approx(1.25)
    assert row["converged"] and row["verdict"] == "converged within 1.3%"
    row = MS.make_row("Q", "periods", "T1", "T1", "time", [1, 2, 4],
                      [0.8, 0.95, 0.9875], 1.0)
    assert not row["converged"]
    assert row["verdict"] == "not converged (est. error 1.3% > 1%)"
    row = MS.make_row("Q", "periods", "T1", "T1", "time", [1, 2],
                      [2.0, 2.0], 1.0)
    assert row["converged"] and row["verdict"] == "converged within <0.01%"


def test_parameter_validation():
    m, _ = ss_plate()
    with pytest.raises(ValueError, match="at least 2"):
        MS.mesh_study(m, levels=[2, 2])
    with pytest.raises(ValueError, match="integers"):
        MS.mesh_study(m, levels=[1, 2.5])
    with pytest.raises(ValueError, match="integers"):
        MS.mesh_study(m, levels=[0, 2])
    with pytest.raises(ValueError, match="unknown load case"):
        MS.mesh_study(m, cases=["NOPE"])
    with pytest.raises(ValueError, match="num_modes"):
        MS.mesh_study(m, cases=["MODAL"])
    with pytest.raises(ValueError, match="quantit"):
        MS.mesh_study(m, quantities=["stress"])
    with pytest.raises(ValueError, match="nothing to study"):
        MS.mesh_study(m, cases=["Q"], quantities=["periods"])
    with pytest.raises(ValueError, match="tolerance_pct"):
        MS.mesh_study(m, tolerance_pct=0)
    with pytest.raises(ValueError, match="max_elements"):
        MS.mesh_study(m, levels=[1, 8], max_elements=100)
    m.cases["Q"].pdelta = True
    with pytest.raises(ValueError, match="not a linear static"):
        MS.mesh_study(m, cases=["Q"])
    assert MS.default_cases(m) == []


def test_default_cases_follow_set_cases_to_run():
    m = portal_with_slab()
    m.num_modes = 3
    m.add_case("L2", {"L": 2.0})
    assert MS.default_cases(m) == ["L", "L2", "MODAL"]
    m.cases_not_run = ["L2", "MODAL"]
    assert MS.default_cases(m) == ["L"]


# --------------------------------------------------------------------------- #
# refinement
# --------------------------------------------------------------------------- #
def test_refine_model_is_nested_and_never_mutates():
    m = portal_with_slab()
    m.add_member("brace", "COL", (0, 0, 0), (6, 0, 3), story="Story1",
                 uid="T1", axial_limit="tension")
    before = _js(m)
    r1 = MS.refine_model(m, 1)
    assert _js(r1) == before and r1 is not m
    r3 = MS.refine_model(m, 3)
    assert _js(m) == before                       # original untouched
    s = r3.shells[0]
    assert s.mesh_divisions == {"n1": 18, "n2": 12}  # 3 x (6, 4)
    mm1, mm3 = mesh_model(m), mesh_model(r3)
    assert len(mm3.quads) == 9 * len(mm1.quads)
    b1 = next(x for x in r3.members if x.uid == "B1")
    assert b1.auto_mesh == {"at_intermediate_joints": False,
                            "at_intersections": False,
                            "max_length": None, "min_segments": 3}
    assert next(x for x in r3.members if x.uid == "T1").auto_mesh is None
    # level-1 nodes survive in the refined mesh (nested refinement)
    k1 = {tuple(round(v, 6) for v in p) for p in mm1.points}
    k3 = {tuple(round(v, 6) for v in p) for p in mm3.points}
    assert k1 <= k3


def test_refine_model_scales_user_options():
    m = portal_with_slab()
    m.frame_auto_mesh = {"at_intermediate_joints": True,
                         "at_intersections": False, "max_length": 1.5,
                         "min_segments": 2}
    m.members[0].auto_mesh = {"at_intermediate_joints": False,
                              "at_intersections": True,
                              "max_length": None, "min_segments": None}
    m.shells[0].mesh_divisions = {"n1": 5, "n2": 3}
    r = MS.refine_model(m, 2)
    assert r.shells[0].mesh_divisions == {"n1": 10, "n2": 6}
    assert r.members[0].auto_mesh["min_segments"] == 2
    assert r.members[0].auto_mesh["at_intersections"] is True
    b1 = next(x for x in r.members if x.uid == "B1")
    assert b1.auto_mesh == {"at_intermediate_joints": True,
                            "at_intersections": False, "max_length": 0.75,
                            "min_segments": 4}
    # polygon region: max size halves
    p = BuildingModel(name="poly")
    p.add_material(Material("C", E=E_C))
    p.add_shell_section(ShellSection("SL", "C", 0.2))
    p.set_stories([3.0])
    p.add_shell("slab", "shell", "SL",
                [(0, 0, 3), (8, 0, 3), (8, 4, 3), (4, 4, 3), (4, 8, 3),
                 (0, 8, 3)], mesh_size=1.0, story="Story1", uid="L")
    rp = MS.refine_model(p, 4)
    assert rp.shells[0].mesh_size == 0.25
    assert rp.shells[0].mesh_divisions is None
    assert p.shells[0].mesh_size == 1.0


def test_level_one_reproduces_a_normal_run_exactly():
    m = portal_with_slab()
    res = MS.mesh_study(m, levels=[1, 2], cases=["L"])
    direct = OpenSeesEngine(m).run_static("L")
    assert _row(res, "L", "base:FZ")["values"][0] == direct.base["FZ"]
    mmax = max(abs(x) for st in direct.member_stations.values()
               for k in ("M2", "M3") for x in st[k])
    assert _row(res, "L", "max_frame_moment")["values"][0] == mmax
    smax = max(max(abs(v[3]), abs(v[4]))
               for v in direct.shell_forces.values())
    assert _row(res, "L", "max_shell_moment")["values"][0] == smax
    # static equilibrium: base FZ = total load at every level
    assert _row(res, "L", "base:FZ")["values"] == pytest.approx(
        [50.0 + 5.0 * 24.0] * 2, rel=1e-9)
    assert res["mesh"][1]["n_shell_elements"] == 4 * \
        res["mesh"][0]["n_shell_elements"]


# --------------------------------------------------------------------------- #
# closed-form validation
# --------------------------------------------------------------------------- #
def test_ss_plate_converges_richardson_within_1pct_of_timoshenko():
    a, q = 4.0, 10.0
    m, D = ss_plate(mesh=1.0, a=a, q=q)
    before = _js(m)
    res = MS.mesh_study(m, levels=[1, 2, 4], cases=["Q"])
    assert _js(m) == before                       # saved model untouched
    w_ex = 0.00406 * q * a ** 4 / D
    row = _row(res, "Q", "roof_disp:2")
    assert row["point"] == [2.0, 2.0, 3.0]         # plate centre tracked
    w = [-v for v in row["values"]]
    assert w[0] < w[1] < w[2]                     # monotone from below
    assert abs(row["rel_change_pct"][2]) < abs(row["rel_change_pct"][1])
    assert -row["richardson"] / w_ex == pytest.approx(1.0, abs=0.01)
    assert abs(w[2] / w_ex - 1) < abs(w[0] / w_ex - 1)
    assert 1.0 < row["order"] < 3.0 and row["converged"]
    # max moment 0.0479 q a^2 (nu = 0.3)
    mrow = _row(res, "Q", "max_shell_moment")
    assert mrow["values"][0] < mrow["values"][1] < mrow["values"][2]
    assert mrow["richardson"] / (0.0479 * q * a ** 2) == pytest.approx(
        1.0, abs=0.01)
    # equilibrium on every level
    assert _row(res, "Q", "base:FZ")["values"] == pytest.approx(
        [q * a * a] * 3, rel=1e-9)
    assert res["summary"]["n_rows"] == len(res["rows"])


def test_ss_plate_fundamental_period_converges():
    a, t, nu = 4.0, 0.05, 0.3
    m, D = ss_plate(mesh=1.0, a=a, t=t, nu=nu)
    m.materials["C"].unit_weight = 24.0
    m.pattern("DEAD", "dead").self_weight_factor = 1.0
    m.mass_source = {"DEAD": 1.0}
    m.mass_options = dict(m.mass_options, include_vertical=True)
    m.rigid_diaphragms = False
    m.num_modes = 3
    res = MS.mesh_study(m, levels=[1, 2, 4], cases=["MODAL"])
    rho_t = 24.0 / 9.80665 * t
    T_ex = 2 * math.pi / (math.pi ** 2 * 2 / a ** 2 * math.sqrt(D / rho_t))
    row = _row(res, "MODAL", "T1")
    assert row["values"][0] > row["values"][1] > row["values"][2]
    assert row["richardson"] / T_ex == pytest.approx(1.0, abs=0.01)
    assert {r["key"] for r in res["rows"]} == {"T1", "T2", "T3"}


def test_cantilever_wall_tip_converges_to_bending_plus_shear():
    m, d_ex = cantilever_wall(mesh=1.0)
    res = MS.mesh_study(m, levels=[1, 2, 4], cases=["P"],
                        quantities=["roof_disp", "base_reactions"])
    row = _row(res, "P", "roof_disp:0")
    v = row["values"]
    assert v[0] < v[1] < v[2] < d_ex * 1.001       # from below, monotone
    assert abs(v[2] - d_ex) < abs(v[1] - d_ex) < abs(v[0] - d_ex)
    assert row["richardson"] / d_ex == pytest.approx(1.0, abs=0.01)
    assert _row(res, "P", "base:FX")["values"] == pytest.approx(
        [-100.0] * 3, rel=1e-9)
    assert {r["quantity"] for r in res["rows"]} == {"roof_disp",
                                                    "base_reactions"}


def test_frame_only_model_is_mesh_independent():
    """Elastic frame elements are exact: refinement changes nothing."""
    m = BuildingModel(name="frame")
    m.num_modes = 0
    m.add_material(Material("S", E=200e6, nu=0.3, unit_weight=0.0,
                            material_type="steel"))
    m.add_section(FrameSection.rectangular("R", "S", 0.2, 0.4))
    m.set_stories([3.0])
    m.add_member("column", "R", (0, 0, 0), (0, 0, 3), uid="C1")
    m.add_member("column", "R", (6, 0, 0), (6, 0, 3), uid="C2")
    m.add_member("beam", "R", (0, 0, 3), (6, 0, 3), uid="B1")
    p = m.pattern("L")
    p.member_loads.append(MemberLoad("B1", kind="point", w=30.0, a=0.3))
    m.add_case("L", {"L": 1.0})
    res = MS.mesh_study(m, levels=[1, 2, 4])
    assert res["mesh"][2]["n_frame_segments"] == 12
    for r in res["rows"]:
        assert r["values"] == pytest.approx([r["values"][0]] * 3,
                                            rel=1e-8, abs=1e-12)
        assert r["converged"]


# --------------------------------------------------------------------------- #
# accuracy advisor
# --------------------------------------------------------------------------- #
def _codes(res):
    return {a["code"] for a in res["advice"]}


def _adv(res, code):
    return next(a for a in res["advice"] if a["code"] == code)


def test_clean_plate_has_no_advice():
    m, _ = ss_plate(mods={"m11": 0.25, "m22": 0.25, "m12": 0.25})
    res = accuracy_advice(m)
    assert res["advice"] == [] and res["summary"]["ok"]
    assert res["thresholds"] == DEFAULTS


def test_element_metrics_closed_form():
    a, w, s = element_metrics([(0, 0, 0), (4, 0, 0), (4, 1, 0), (0, 1, 0)])
    assert (a, w, s) == (4.0, 0.0, 4.0)
    a, w, s = element_metrics([(0, 0, 0), (1, 0, 0), (1, 1, 0.1),
                               (0, 1, 0)])
    nrm = math.sqrt(0.1 ** 2 * 2 + 4)
    assert w == pytest.approx((0.05 / nrm) / math.sqrt(nrm / 2), rel=1e-12)
    assert s == pytest.approx(math.sqrt(1.01))
    m, _ = ss_plate(a=4.0)
    assert region_span(m.shells[0]) == pytest.approx(4.0)


def test_aspect_ratio_and_coarse_mesh():
    m, _ = ss_plate(mods={"m11": 0.3, "m22": 0.3, "m12": 0.3})
    m.shells[0].mesh_divisions = {"n1": 1, "n2": 8}      # 4 x 0.5 elements
    res = accuracy_advice(m)
    assert _codes(res) == {"SHELL_ASPECT_RATIO", "SHELL_MESH_COARSE"}
    asp = _adv(res, "SHELL_ASPECT_RATIO")
    assert asp["objects"] == ["S1"] and asp["refs"] == [
        {"type": "shell", "uid": "S1"}]
    assert asp["details"][0]["max_aspect"] == pytest.approx(8.0)
    assert asp["details"][0]["n_elements"] == 8
    co = _adv(res, "SHELL_MESH_COARSE")
    assert co["details"][0]["elements_across"] == pytest.approx(1.0)
    assert {"title", "message", "explanation", "fix"} <= set(co)
    # exactly at the limit (4 across) is fine; the threshold is a knob
    m.shells[0].mesh_divisions = {"n1": 4, "n2": 4}
    assert accuracy_advice(m)["advice"] == []
    res = accuracy_advice(m, {"min_elements_per_span": 5})
    assert _codes(res) == {"SHELL_MESH_COARSE"}
    with pytest.raises(ValueError):
        accuracy_advice(m, {"bogus": 1})
    with pytest.raises(ValueError):
        accuracy_advice(m, {"aspect_max": -1})


def test_warped_quads_on_lenient_model():
    m, _ = ss_plate(mods={"m11": 0.3, "m22": 0.3, "m12": 0.3})
    d = m.to_dict()
    d["shells"][0]["corners"][2][2] = 5.0             # lift one corner
    bad = load_model_lenient(d)
    res = accuracy_advice(bad)
    assert "SHELL_WARPED" in _codes(res)
    assert _adv(res, "SHELL_WARPED")["objects"] == ["S1"]


def test_membrane_slab_bending_cases():
    m = portal_with_slab()
    m.shells[0].behavior = "membrane"                 # beams on y=0 / y=4
    res = accuracy_advice(m)
    adv = _adv(res, "MEMBRANE_SLAB_BENDING")
    assert adv["objects"] == ["S1"]
    assert adv["details"][0]["edges"] == [1, 3]       # x = 6 and x = 0
    m.add_member("beam", "BM", (6, 0, 3), (6, 4, 3), uid="B3")
    m.add_member("beam", "BM", (0, 4, 3), (0, 0, 3), uid="B4")
    assert "MEMBRANE_SLAB_BENDING" not in _codes(accuracy_advice(m))
    # meshed shell with membrane-only stiffness carrying an area load
    m2 = portal_with_slab()
    m2.shell_sections["SL"].shell_type = "membrane"
    adv = _adv(accuracy_advice(m2), "MEMBRANE_SLAB_BENDING")
    assert adv["details"][0]["reason"] == "membrane_type_out_of_plane_load"


def test_plate_theory_checks():
    m, _ = ss_plate(t=0.6, shell_type="shell_thin",
                    mods={"m11": 0.3, "m22": 0.3, "m12": 0.3})
    adv = _adv(accuracy_advice(m), "PLATE_THIN_ON_THICK")
    assert adv["severity"] == "warning"
    assert adv["details"][0]["t_over_span"] == pytest.approx(0.15)
    m, _ = ss_plate(t=0.05, shell_type="shell_thick",
                    mods={"m11": 0.3, "m22": 0.3, "m12": 0.3})
    res = accuracy_advice(m)
    assert _codes(res) == {"PLATE_THICK_ON_THIN"}
    assert _adv(res, "PLATE_THICK_ON_THIN")["severity"] == "info"
    m, _ = ss_plate(t=0.2, shell_type="shell_thick",
                    mods={"m11": 0.3, "m22": 0.3, "m12": 0.3})
    assert accuracy_advice(m)["advice"] == []


def test_rigid_diaphragm_with_large_openings():
    m = portal_with_slab()
    m.shells[0].openings = [Opening(0.2, 0.2, 0.8, 0.8)]   # 36 %
    adv = _adv(accuracy_advice(m), "RIGID_DIAPHRAGM_OPENINGS")
    assert adv["details"][0]["opening_ratio"] == pytest.approx(0.36)
    m.shells[0].openings = [Opening(0.4, 0.4, 0.6, 0.6)]   # 4 %
    assert "RIGID_DIAPHRAGM_OPENINGS" not in _codes(accuracy_advice(m))
    m.shells[0].openings = [Opening(0.2, 0.2, 0.8, 0.8)]
    m.story_diaphragm = {"Story1": "none"}
    assert "RIGID_DIAPHRAGM_OPENINGS" not in _codes(accuracy_advice(m))
    m.diaphragms = {"D1": {"type": "rigid"}}
    m.shells[0].diaphragm = "D1"
    assert "RIGID_DIAPHRAGM_OPENINGS" in _codes(accuracy_advice(m))
    m.diaphragms = {"D1": {"type": "semi_rigid"}}
    assert "RIGID_DIAPHRAGM_OPENINGS" not in _codes(accuracy_advice(m))


def test_cracked_modifiers():
    m = portal_with_slab()
    adv = _adv(accuracy_advice(m), "CRACKED_MODIFIERS_MISSING")
    assert set(adv["objects"]) == {"C0", "C1", "C2", "C3", "B1", "B2",
                                   "S1"}
    assert {"type": "shell", "uid": "S1"} in adv["refs"]
    m.sections["COL"].mod_I33 = m.sections["COL"].mod_I22 = 0.7
    m.sections["BM"].mod_I33 = 0.35
    m.shell_sections["SL"].m11 = 0.25
    assert "CRACKED_MODIFIERS_MISSING" not in _codes(accuracy_advice(m))
    s = portal_with_slab()
    s.materials["C"].material_type = "steel"
    assert "CRACKED_MODIFIERS_MISSING" not in _codes(accuracy_advice(s))
    s = portal_with_slab()
    s.materials["C"].E = 200e6           # mislabeled steel: not concrete
    assert "CRACKED_MODIFIERS_MISSING" not in _codes(accuracy_advice(s))


def test_mass_source_missing():
    m = portal_with_slab()
    m.num_modes = 6
    adv = _adv(accuracy_advice(m), "MASS_SOURCE_MISSING")
    assert adv["objects"] == [] and adv["count"] == 1
    m.cases_not_run = ["MODAL"]
    assert "MASS_SOURCE_MISSING" not in _codes(accuracy_advice(m))
    m.cases_not_run = []
    m.materials["C"].unit_weight = 24.0
    m.pattern("DEAD", "dead").self_weight_factor = 1.0
    m.mass_source = {"DEAD": 1.0}
    assert "MASS_SOURCE_MISSING" not in _codes(accuracy_advice(m))
    m.mass_source = {}
    m.story_masses = {"Story1": 10.0}
    assert "MASS_SOURCE_MISSING" not in _codes(accuracy_advice(m))


def test_frame_point_load_without_automesh():
    m = portal_with_slab()
    adv = _adv(accuracy_advice(m), "FRAME_POINT_LOAD_NO_AUTOMESH")
    assert adv["objects"] == ["B1"]
    assert adv["details"][0]["load_positions"] == [0.5]
    m.members[4].auto_mesh = {"at_intermediate_joints": True}
    assert "FRAME_POINT_LOAD_NO_AUTOMESH" not in _codes(accuracy_advice(m))
    # a loaded beam that touches no shell is fine
    f = portal_with_slab()
    f.shells = []
    f.patterns["L"].area_loads = []
    assert "FRAME_POINT_LOAD_NO_AUTOMESH" not in _codes(accuracy_advice(f))


def test_advisor_never_mutates_and_sorts_by_severity():
    m, _ = ss_plate(t=0.05, shell_type="shell_thick")
    m.shells[0].mesh_divisions = {"n1": 1, "n2": 8}
    before = _js(m)
    res = accuracy_advice(m)
    assert _js(m) == before
    sev = [a["severity"] for a in res["advice"]]
    assert sev == sorted(sev, key=["warning", "info"].index)
    assert res["summary"]["warnings"] + res["summary"]["info"] == len(sev)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path))
    from skyframe.api import server as srv
    app = srv.create_app()
    app.config["TESTING"] = True
    old = srv._state["model"]
    with app.test_client() as c:
        yield c, srv, tmp_path
    srv._state["model"] = old


def test_api_mesh_study_does_not_touch_current_or_saved_model(client):
    c, srv, tmp = client
    m, D = ss_plate()
    assert c.post("/api/model", json=m.to_dict()).status_code == 200
    assert c.post("/api/models/plate").status_code == 200
    saved = (tmp / "plate.skyframe.json").read_bytes()
    cur = c.get("/api/model").get_data()
    r = c.post("/api/analyze/mesh_study",
               json={"levels": [1, 2, 4], "cases": ["Q"],
                     "quantities": ["roof_disp"]})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert set(body) == {"levels", "cases", "quantities", "tolerance_pct",
                         "mesh", "rows", "summary", "warnings"}
    row = body["rows"][0]
    assert -row["richardson"] / (0.00406 * 10 * 4 ** 4 / D) == \
        pytest.approx(1.0, abs=0.01)
    assert c.get("/api/model").get_data() == cur
    assert (tmp / "plate.skyframe.json").read_bytes() == saved
    # body model wins over the current model; errors are 400
    w, _ = cantilever_wall()
    r = c.post("/api/analyze/mesh_study",
               json={"model": w.to_dict(), "levels": [1, 2],
                     "quantities": ["roof_disp"]})
    assert r.status_code == 200
    assert r.get_json()["cases"] == ["P"]
    assert c.get("/api/model").get_data() == cur
    r = c.post("/api/analyze/mesh_study", json={"levels": [3]})
    assert r.status_code == 400 and "error" in r.get_json()
    r = c.post("/api/analyze/mesh_study", json={"cases": ["X"]})
    assert r.status_code == 400


def test_api_accuracy_advice_get_and_post(client):
    c, srv, _ = client
    m = portal_with_slab()
    c.post("/api/model", json=m.to_dict())
    cur = c.get("/api/model").get_data()
    r = c.get("/api/accuracy_advice")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"advice", "summary", "thresholds"}
    assert {"CRACKED_MODIFIERS_MISSING",
            "FRAME_POINT_LOAD_NO_AUTOMESH"} <= _codes(body)
    a = body["advice"][0]
    assert set(a) == {"severity", "code", "title", "message", "explanation",
                      "fix", "count", "objects", "refs", "details"}
    r = c.post("/api/accuracy_advice",
               json={"thresholds": {"min_elements_per_span": 10}})
    assert "SHELL_MESH_COARSE" in _codes(r.get_json())
    p, _ = ss_plate(mods={"m11": 0.3, "m22": 0.3, "m12": 0.3})
    r = c.post("/api/accuracy_advice", json={"model": p.to_dict()})
    assert r.get_json()["advice"] == []
    r = c.post("/api/accuracy_advice", json={"thresholds": {"x": 1}})
    assert r.status_code == 400
    assert c.get("/api/model").get_data() == cur
