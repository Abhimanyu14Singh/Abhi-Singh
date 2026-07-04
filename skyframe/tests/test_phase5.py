"""Phase 5 backend tests: envelope combos, mass source, stiffness modifiers,
auto wind patterns, linear time-history analysis, column orientation angle,
and shell element forces.

Same rigor as test_phase4.py: every expected number is derived in the test
from first principles — closed-form cantilever/SDOF mechanics, an
independent numpy Newmark integrator, the ASCE 7 velocity-pressure formulas
recomputed with numpy, and section-cut equilibrium for shell resultants.

Envelope combos:
  * per-quantity min/max of the FACTORED case results, checked element by
    element (1e-12) against max/min computed from the individual case
    results; a single-case envelope equals that case as both min and max.

Mass source:
  * story mass for {"DEAD": 1, "LIVE": 0.25} equals the hand value
    sum(w*L)*(1 + 0.25*r)/g exactly; every modal period scales by
    sqrt(m2/m1) (1e-9 on the ratio) because the mass matrix scales
    uniformly while K is unchanged.

Stiffness modifiers:
  * mod_I33 = 0.5 doubles the cantilever tip deflection EXACTLY (1e-9) and
    the absolute value matches PL^3/(3E*I_eff); mod_A = 0.5 doubles axial
    shortening; ShellSection.mod = 0.5 doubles a shear wall's drift (the
    modifier scales E, so the whole shell stiffness matrix scales).

Wind (ASCE 7-16 style):
  * Kz cross-checked against the published Table 26.10-1 value for
    Exposure C at z = 30 ft (9.14 m): Kz = 0.98; story forces recomputed
    in numpy from qz = 0.613*Kz*Kzt(1.0)*Kd(0.85)*V^2*I [Pa -> kPa] and
    tributary facade areas; base shear equals the exact force sum.

Time history:
  * the engine's transient response of an exact SDOF (cantilever column +
    tip mass) under near-resonant (0.8*omega_n) sinusoidal ground motion
    is compared to an INDEPENDENT numpy Newmark (const-average) integrator
    of the same 2-DOF (u, theta) element model with the same Rayleigh
    C = a0*M + a1*K — peak agreement required at 1% (observed ~1e-12);
  * free-vibration log decrement recovers zeta = 0.05 within 5%;
  * peak base shear ~= k * peak displacement within 2% (at the
    displacement peak the velocity ~ 0, so the damping force vanishes and
    the static reaction k*u is the whole base shear).

Orientation angle:
  * a rectangular column's sway periods follow T = 2*pi*sqrt(m L^3/(3EI))
    with I22 for X and I33 for Y at angle = 0 (1e-9), and angle = 90
    swaps the directions exactly.

Shell forces:
  * cantilever wall under in-plane lateral P: a horizontal section cut
    through a row of quads carries sum(Nxy * width) = P (2% mesh
    tolerance); one-way slab strip under UDL: sum(Mxx * width) across a
    cut matches the beam-statics moment q*W*x*(L-x)/2 within 3%, with a
    consistent sign across the cut.

Units per CONTRACT.md: kN, m, tonne, s; E in kPa.
"""

import math

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine

from skyframe.core.builder import (make_wind_pattern, quick_building,
                                   wind_kz, wind_qz)
from skyframe.core.model import (
    G_ACCEL,
    AreaLoad,
    BuildingModel,
    FrameSection,
    Material,
    MemberUDL,
    NodalLoad,
    NodalMass,
    PointSupport,
    ShellRegion,
    ShellSection,
)

E_CONC = 25_000_000.0  # kPa


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _run(model) -> dict:
    return OpenSeesEngine(model).run().to_dict()


def _find_node(results: dict, pt, tol=1e-6):
    for tag, xyz in results["nodes"].items():
        if all(abs(a - b) < tol for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(f"no FE node found at {pt}")


def _new_model(name: str, story_heights) -> BuildingModel:
    mdl = BuildingModel(name=name)
    mdl.rigid_diaphragms = False
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.set_stories(list(story_heights))
    return mdl


def _sdof_column(m=10.0, L=3.0, size=0.3) -> BuildingModel:
    """Cantilever column, tip mass on ux only: exact SDOF, k = 3EI/L^3."""
    mdl = _new_model("SDOF", [L])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", size, size))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, L), mx=m))
    mdl.num_modes = 1
    return mdl


def _mesh_points(model):
    from skyframe.core.mesh import mesh_model
    return mesh_model(model).points


def _newmark_2dof(K, M, C, p_of_step, dt, n, gamma=0.5, beta=0.25):
    """Independent Newmark const-average integrator (u at each step end).

    ``p_of_step(k)`` is the load vector solved for at t = (k+1)*dt —
    identical convention to the engine (implicit step to the new time).
    Handles a singular M (massless rotational DOF) because the effective
    stiffness K + gamma/(beta dt) C + 1/(beta dt^2) M stays regular.
    """
    ndof = K.shape[0]
    u = np.zeros(ndof)
    v = np.zeros(ndof)
    a = np.zeros(ndof)
    Keff = K + gamma / (beta * dt) * C + M / (beta * dt * dt)
    out = np.zeros((n, ndof))
    for k in range(n):
        p = p_of_step(k)
        rhs = (p + M @ (u / (beta * dt * dt) + v / (beta * dt)
                        + (1.0 / (2.0 * beta) - 1.0) * a)
               + C @ (gamma / (beta * dt) * u
                      - (1.0 - gamma / beta) * v
                      - dt * (1.0 - gamma / (2.0 * beta)) * a))
        un = np.linalg.solve(Keff, rhs)
        an = ((un - u) / (beta * dt * dt) - v / (beta * dt)
              - (1.0 / (2.0 * beta) - 1.0) * a)
        vn = v + dt * ((1.0 - gamma) * a + gamma * an)
        u, v, a = un, vn, an
        out[k] = u
    return out


# --------------------------------------------------------------------------- #
# 1. envelope combos
# --------------------------------------------------------------------------- #
def _envelope_model():
    """Cantilever column with two opposite-sign lateral patterns.

    Every response quantity is linear in the tip load, so case B's results
    are exactly -0.4x case A's — max must pick A wherever A > 0 and B
    wherever A < 0, per individual entry.
    """
    mdl = _sdof_column()
    mdl.pattern("A", "other").nodal_loads.append(NodalLoad((0, 0, 3.0),
                                                           fx=10.0))
    mdl.pattern("B", "other").nodal_loads.append(NodalLoad((0, 0, 3.0),
                                                           fx=-4.0))
    mdl.add_case("A", {"A": 1.0})
    mdl.add_case("B", {"B": 1.0})
    mdl.add_combo("ENV", {"A": 1.0, "B": 1.0}, combo_type="envelope")
    mdl.add_combo("ENV1", {"A": 1.0}, combo_type="envelope")
    mdl.add_combo("ENVF", {"A": 2.0}, combo_type="envelope")
    return mdl


def _assert_case_shaped_envelope(env, case_dicts, agg, tol=1e-12):
    """Every entry of ``env`` equals ``agg`` (max or min) over the cases."""
    for block in ("node_disp", "reactions", "member_forces"):
        for key, vec in env[block].items():
            for i, v in enumerate(vec):
                want = agg(c[block][key][i] for c in case_dicts)
                assert abs(v - want) <= tol, (block, key, i, v, want)
    for key, v in env["base"].items():
        want = agg(c["base"][key] for c in case_dicts)
        assert abs(v - want) <= tol, ("base", key)
    for s, vals in env["story"].items():
        for key, v in vals.items():
            want = agg(c["story"][s][key] for c in case_dicts)
            assert abs(v - want) <= tol, ("story", s, key)
    for uid, st in env["member_stations"].items():
        assert st["x"] == case_dicts[0]["member_stations"][uid]["x"]
        for key in ("N", "V2", "V3", "T", "M2", "M3"):
            for i, v in enumerate(st[key]):
                want = agg(c["member_stations"][uid][key][i]
                           for c in case_dicts)
                assert abs(v - want) <= tol, ("stations", uid, key, i)


def test_envelope_combo_min_max_per_quantity():
    d = _run(_envelope_model())
    a, b = d["cases"]["A"], d["cases"]["B"]
    env = d["combos"]["ENV"]
    assert "min" in env
    # max block picks max(A, B) entrywise; "min" block picks min(A, B)
    _assert_case_shaped_envelope(env, [a, b], max)
    _assert_case_shaped_envelope(env["min"], [a, b], min)
    # the min block carries the full case shape
    assert set(env["min"]) >= {"node_disp", "reactions", "base",
                               "member_forces", "story", "member_stations"}
    # sanity on actual numbers: tip ux max comes from A, min from B
    tip = _find_node(d, (0, 0, 3.0))
    assert env["node_disp"][tip][0] == pytest.approx(
        a["node_disp"][tip][0], rel=1e-12)
    assert env["min"]["node_disp"][tip][0] == pytest.approx(
        b["node_disp"][tip][0], rel=1e-12)
    # opposite-sign responses really were exercised
    assert a["node_disp"][tip][0] > 0.0 > b["node_disp"][tip][0]


def test_envelope_single_case_and_factors():
    d = _run(_envelope_model())
    a = d["cases"]["A"]
    env1 = d["combos"]["ENV1"]
    # envelope of one case == that case, as both max and min
    _assert_case_shaped_envelope(env1, [a], max)
    _assert_case_shaped_envelope(env1["min"], [a], min)
    # factors are applied BEFORE the envelope: ENVF = 2.0 x A on both sides
    two_a = {  # factored copy of the blocks the checker reads
        "node_disp": {k: [2 * x for x in v]
                      for k, v in a["node_disp"].items()},
        "reactions": {k: [2 * x for x in v]
                      for k, v in a["reactions"].items()},
        "member_forces": {k: [2 * x for x in v]
                          for k, v in a["member_forces"].items()},
        "base": {k: 2 * v for k, v in a["base"].items()},
        "story": {s: {k: 2 * v for k, v in vals.items()}
                  for s, vals in a["story"].items()},
        "member_stations": {
            uid: {"x": st["x"],
                  **{k: [2 * x for x in st[k]]
                     for k in ("N", "V2", "V3", "T", "M2", "M3")}}
            for uid, st in a["member_stations"].items()},
    }
    envf = d["combos"]["ENVF"]
    _assert_case_shaped_envelope(envf, [two_a], max)
    _assert_case_shaped_envelope(envf["min"], [two_a], min)
    # additive combos are untouched: no "min" key
    assert "min" not in d["cases"]["A"]


def test_envelope_validation():
    mdl = _envelope_model()
    with pytest.raises(ValueError, match="combo_type"):
        mdl.add_combo("BAD", {"A": 1.0}, combo_type="envelop")
    with pytest.raises(ValueError, match="at least one case"):
        mdl.add_combo("EMPTY", {}, combo_type="envelope")
    mdl.add_th_case("TH", "X", [0.0, 1.0], 0.01)
    with pytest.raises(ValueError, match="time-history"):
        mdl.add_combo("BADTH", {"TH": 1.0})
    # combo_type round-trips
    d2 = BuildingModel.from_dict(mdl.to_dict())
    assert d2.combos["ENV"].combo_type == "envelope"
    assert d2.combos["ENV1"].combo_type == "envelope"


# --------------------------------------------------------------------------- #
# 2. mass source
# --------------------------------------------------------------------------- #
def _portal(mass_source=None, mass_from_patterns=None):
    """One-bay portal frame in the x-z plane with a rigid diaphragm.

    DEAD w = 12 kN/m and LIVE w = 8 kN/m on the single 6 m beam.
    """
    mdl = BuildingModel(name="portal")
    mdl.add_material(Material("CONC", E=E_CONC, nu=0.2))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.3))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.5))
    mdl.set_stories([3.0])
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1")
    mdl.add_member("column", "COL", (6, 0, 0), (6, 0, 3), story="Story1",
                   uid="C2")
    mdl.add_member("beam", "BEAM", (0, 0, 3), (6, 0, 3), story="Story1",
                   uid="B1")
    dead = mdl.pattern("DEAD", "dead")
    live = mdl.pattern("LIVE", "live")
    dead.member_udls.append(MemberUDL("B1", 12.0))
    live.member_udls.append(MemberUDL("B1", 8.0))
    if mass_source is not None:
        mdl.mass_source = dict(mass_source)
    if mass_from_patterns is not None:
        mdl.mass_from_patterns = dict(mass_from_patterns)
    mdl.num_modes = 3
    return mdl


def test_mass_source_hand_value_and_period_shift():
    wd, wl, Lb = 12.0, 8.0, 6.0
    m1_hand = wd * Lb / G_ACCEL                       # DEAD only
    m2_hand = (wd + 0.25 * wl) * Lb / G_ACCEL         # DEAD + 0.25 LIVE

    mdl1 = _portal(mass_source={"DEAD": 1.0})
    mdl2 = _portal(mass_source={"DEAD": 1.0, "LIVE": 0.25})
    assert mdl1.compute_story_masses()["Story1"] == pytest.approx(
        m1_hand, rel=1e-12)
    assert mdl2.compute_story_masses()["Story1"] == pytest.approx(
        m2_hand, rel=1e-12)

    # K unchanged, M scales uniformly (diaphragm ux/uy/rz masses are all
    # proportional to the story mass) -> EVERY period scales by sqrt(m2/m1)
    T1 = OpenSeesEngine(mdl1).run_modal().periods
    T2 = OpenSeesEngine(mdl2).run_modal().periods
    assert len(T1) == len(T2) == 3
    ratio = math.sqrt(m2_hand / m1_hand)
    for t1, t2 in zip(T1, T2):
        assert t2 / t1 == pytest.approx(ratio, rel=1e-9)


def test_mass_source_backward_compatibility():
    # legacy field alone still works ...
    legacy = _portal(mass_from_patterns={"DEAD": 1.0, "LIVE": 0.25})
    modern = _portal(mass_source={"DEAD": 1.0, "LIVE": 0.25})
    assert legacy.compute_story_masses() == modern.compute_story_masses()
    # ... and mass_source WINS when both are set
    both = _portal(mass_source={"DEAD": 1.0},
                   mass_from_patterns={"DEAD": 1.0, "LIVE": 0.25})
    assert both.compute_story_masses()["Story1"] == pytest.approx(
        12.0 * 6.0 / G_ACCEL, rel=1e-12)

    # a pre-v0.4 dict (no "mass_source" key) falls back to the legacy field
    d = legacy.to_dict()
    d.pop("mass_source")
    d["story_masses"] = {}          # force re-derivation from patterns
    back = BuildingModel.from_dict(d)
    assert back.compute_story_masses()["Story1"] == pytest.approx(
        (12.0 + 0.25 * 8.0) * 6.0 / G_ACCEL, rel=1e-12)

    # quick_building declares the v0.4 mass source
    assert quick_building(stories=1).mass_source == {"DEAD": 1.0}


# --------------------------------------------------------------------------- #
# 3. stiffness modifiers
# --------------------------------------------------------------------------- #
def _cantilever_beam(mod_I33=1.0, mod_A=1.0):
    mdl = _new_model("cant", [3.0])
    sec = FrameSection.rectangular("B", "CONC", 0.3, 0.6)
    sec.mod_I33 = mod_I33
    sec.mod_A = mod_A
    mdl.add_section(sec)
    mdl.add_member("beam", "B", (0, 0, 3), (4, 0, 3), story="Story1",
                   uid="B1")
    mdl.supports.append(PointSupport((0, 0, 3), (1, 1, 1, 1, 1, 1)))
    mdl.pattern("P", "other").nodal_loads.append(
        NodalLoad((4, 0, 3), fz=-10.0))
    mdl.pattern("N", "other").nodal_loads.append(
        NodalLoad((4, 0, 3), fx=-100.0))
    mdl.add_case("P", {"P": 1.0})
    mdl.add_case("N", {"N": 1.0})
    mdl.num_modes = 0
    return mdl


def test_frame_stiffness_modifiers_exact():
    P, L = 10.0, 4.0
    I33 = 0.3 * 0.6 ** 3 / 12.0
    A = 0.3 * 0.6

    def tip_disp(**mods):
        eng = OpenSeesEngine(_cantilever_beam(**mods))
        rP = eng.run_static("P")
        rN = eng.run_static("N")
        tip = next(t for t, c in eng._asm.node_coords.items()
                   if abs(c[0] - L) < 1e-9)
        return rP.node_disp[tip][2], rN.node_disp[tip][0]

    uz1, ux1 = tip_disp()
    uz2, ux2 = tip_disp(mod_I33=0.5)
    uz3, ux3 = tip_disp(mod_A=0.5)

    # bending: exact PL^3/(3 E I_eff); mod_I33 = 0.5 doubles it exactly
    assert uz1 == pytest.approx(-P * L ** 3 / (3 * E_CONC * I33), rel=1e-9)
    assert uz2 / uz1 == pytest.approx(2.0, rel=1e-9)
    assert uz2 == pytest.approx(-P * L ** 3 / (3 * E_CONC * 0.5 * I33),
                                rel=1e-9)
    # axial: exact NL/(E A_eff); mod_A = 0.5 doubles it; bending unaffected
    assert ux1 == pytest.approx(-100.0 * L / (E_CONC * A), rel=1e-9)
    assert ux3 / ux1 == pytest.approx(2.0, rel=1e-9)
    assert uz3 == pytest.approx(uz1, rel=1e-9)
    assert ux2 == pytest.approx(ux1, rel=1e-9)


def test_shell_modifier_doubles_drift():
    def wall_drift(mod):
        mdl = _new_model("wall", [3.0])
        mdl.add_shell_section(ShellSection("SH", "CONC", 0.2, mod=mod))
        mdl.shells.append(ShellRegion(
            "W1", "wall", "shell", "SH",
            [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)],
            mesh_size=0.5, story="Story1"))
        pts = _mesh_points(mdl)
        top = [p for p in pts if abs(p[2] - 3.0) < 1e-9]
        for p in pts:
            if abs(p[2]) < 1e-9:
                mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
        pat = mdl.pattern("P", "other")
        for p in top:
            pat.nodal_loads.append(NodalLoad(p, fx=50.0 / len(top)))
        mdl.add_case("P", {"P": 1.0})
        mdl.num_modes = 0
        d = _run(mdl)
        return np.mean([d["cases"]["P"]["node_disp"][_find_node(d, p)][0]
                        for p in top])

    u1, u2 = wall_drift(1.0), wall_drift(0.5)
    # mod scales E, so the WHOLE shell stiffness matrix scales: exactly x2
    assert u2 / u1 == pytest.approx(2.0, rel=1e-6)


def test_modifier_validation_and_roundtrip():
    mdl = _new_model("bad", [3.0])
    sec = FrameSection.rectangular("B", "CONC", 0.3, 0.6)
    sec.mod_J = 0.0
    with pytest.raises(ValueError, match="mod_J"):
        mdl.add_section(sec)
    with pytest.raises(ValueError, match="mod"):
        mdl.add_shell_section(ShellSection("SH", "CONC", 0.2, mod=-1.0))

    good = _cantilever_beam(mod_I33=0.35, mod_A=0.7)
    good.sections["B"].mod_I22 = 0.25
    good.sections["B"].mod_J = 0.10
    good.add_shell_section(ShellSection("SH", "CONC", 0.15, mod=0.5))
    back = BuildingModel.from_dict(good.to_dict())
    s = back.sections["B"]
    assert (s.mod_A, s.mod_I33, s.mod_I22, s.mod_J) == (0.7, 0.35, 0.25, 0.1)
    assert back.shell_sections["SH"].mod == 0.5
    # pre-v0.4 dicts (keys absent) default every modifier to 1.0
    d = good.to_dict()
    for key in ("mod_A", "mod_I33", "mod_I22", "mod_J"):
        d["sections"]["B"].pop(key)
    d["shell_sections"]["SH"].pop("mod")
    old = BuildingModel.from_dict(d)
    assert old.sections["B"].mod_I33 == 1.0
    assert old.shell_sections["SH"].mod == 1.0


# --------------------------------------------------------------------------- #
# 4. auto wind pattern
# --------------------------------------------------------------------------- #
def test_wind_kz_published_value():
    # ASCE 7-16 Table 26.10-1, Exposure C, z = 30 ft (9.144 m): Kz = 0.98
    assert wind_kz(9.144, "C") == pytest.approx(0.98, rel=5e-3)
    # power law reproduced independently
    for z, exp, (alpha, zg) in ((25.0, "B", (7.0, 365.76)),
                                (25.0, "C", (9.5, 274.32)),
                                (25.0, "D", (11.5, 213.36))):
        assert wind_kz(z, exp) == pytest.approx(
            2.01 * (z / zg) ** (2.0 / alpha), rel=1e-12)
    # 4.6 m evaluation floor
    assert wind_kz(1.0, "C") == wind_kz(4.6, "C")
    assert wind_kz(3.5, "D") == pytest.approx(
        2.01 * (4.6 / 213.36) ** (2.0 / 11.5), rel=1e-12)
    with pytest.raises(ValueError, match="exposure"):
        wind_kz(10.0, "A")


def test_wind_pattern_story_forces_hand_calc():
    V, cp = 40.0, 1.3
    mdl = quick_building(name="wind", bays_x=2, bay_width_x=5.0,
                         bays_y=1, bay_width_y=4.0, stories=3,
                         story_height=3.5)
    pat = make_wind_pattern(mdl, "WX", "X", V, exposure="C", cp_total=cp)
    assert pat.kind == "wind"
    assert mdl.patterns["WX"] is pat

    # independent numpy recomputation, same published formulas
    alpha, zg = 9.5, 274.32
    elev = np.array([3.5, 7.0, 10.5])
    trib = np.array([3.5, 3.5, 1.75])       # half below + half above; top:
    width = 4.0                             # plan Y extent (wind along X)
    kz = 2.01 * (np.maximum(elev, 4.6) / zg) ** (2.0 / alpha)
    qz = 0.613 * kz * 1.0 * 0.85 * V ** 2 / 1000.0        # kPa
    F = qz * cp * trib * width

    assert len(pat.story_forces) == 3
    for sf, f_hand in zip(pat.story_forces, F):
        assert sf.fx == pytest.approx(f_hand, rel=1e-12)
        assert sf.fy == 0.0
    assert wind_qz(elev[0], V, "C") == pytest.approx(qz[0], rel=1e-12)

    # direction Y loads fy with the perpendicular (X) width = 10 m
    pat_y = make_wind_pattern(mdl, "WY", "Y", V, exposure="C", cp_total=cp)
    Fy = qz * cp * trib * 10.0
    for sf, f_hand in zip(pat_y.story_forces, Fy):
        assert sf.fy == pytest.approx(f_hand, rel=1e-12)
        assert sf.fx == 0.0

    # engine equilibrium: base shear == total wind force exactly; the
    # Story1 reported shear is the full cumulative base shear
    mdl.add_case("WX", {"WX": 1.0})
    d = _run(mdl)
    total = float(F.sum())
    case = d["cases"]["WX"]
    assert case["base"]["FX"] == pytest.approx(-total, rel=1e-9)
    assert case["story"]["Story1"]["shear_x"] == pytest.approx(total,
                                                               rel=1e-12)
    assert case["story"]["Story3"]["shear_x"] == pytest.approx(float(F[2]),
                                                               rel=1e-12)

    with pytest.raises(ValueError, match="direction"):
        make_wind_pattern(mdl, "W", "Z", V)
    with pytest.raises(ValueError, match="basic_wind_speed"):
        make_wind_pattern(mdl, "W", "X", 0.0)


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYFRAME_MODELS_DIR", str(tmp_path / "models"))
    from skyframe.api.server import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_wind_endpoint(api_client):
    r = api_client.post("/api/model/quick",
                        json={"bays_x": 2, "bay_width_x": 5.0, "bays_y": 1,
                              "bay_width_y": 4.0, "stories": 3,
                              "story_height": 3.5})
    assert r.status_code == 200

    r = api_client.post("/api/pattern/wind",
                        json={"name": "W1", "direction": "Y", "V": 40.0,
                              "exposure": "B", "Cp": 1.2})
    assert r.status_code == 200, r.get_json()
    model_dict = r.get_json()
    assert "W1" in model_dict["patterns"]
    pat = model_dict["patterns"]["W1"]
    assert pat["kind"] == "wind"

    # hand recomputation (exposure B, width perpendicular to Y = 10 m)
    alpha, zg = 7.0, 365.76
    elev = np.array([3.5, 7.0, 10.5])
    trib = np.array([3.5, 3.5, 1.75])
    kz = 2.01 * (np.maximum(elev, 4.6) / zg) ** (2.0 / alpha)
    F = 0.613 * kz * 0.85 * 40.0 ** 2 / 1000.0 * 1.2 * trib * 10.0
    got = [sf["fy"] for sf in pat["story_forces"]]
    assert got == pytest.approx(list(F), rel=1e-12)
    assert all(sf["fx"] == 0.0 for sf in pat["story_forces"])

    # bad input -> 400 with an error message
    assert api_client.post("/api/pattern/wind", json={}).status_code == 400
    assert api_client.post("/api/pattern/wind",
                           json={"V": "fast"}).status_code == 400
    assert api_client.post("/api/pattern/wind",
                           json={"V": 40, "exposure": "Z"}).status_code == 400
    assert api_client.post("/api/pattern/wind",
                           json={"V": 40, "direction": "Q"}).status_code == 400
    r = api_client.post("/api/pattern/wind", json={"V": -3.0})
    assert r.status_code == 400 and "error" in r.get_json()


# --------------------------------------------------------------------------- #
# 5. linear time-history analysis
# --------------------------------------------------------------------------- #
def _sdof_th_params(m=10.0, L=3.0, size=0.3):
    I = size ** 4 / 12.0
    k = 3.0 * E_CONC * I / L ** 3
    wn = math.sqrt(k / m)
    return I, k, wn


def _th_numpy_reference(accel, dt, m, L, I, zeta, wn):
    """Independent Newmark run of the SAME 2-DOF (u, theta) column model.

    K is the exact Euler cantilever stiffness condensed to the tip
    (v = tip translation, theta = tip rotation with theta = dv/dz):
    K = EI/L^3 [[12, -6L], [-6L, 4L^2]].  M = diag(m, 0) (massless
    rotation).  C = a0 M + a1 K with the engine's single-mode Rayleigh
    fit a0 = zeta*wn, a1 = zeta/wn.  Ground motion enters as
    p = [-m * ag((k+1) dt), 0] per implicit step, exactly the engine's
    Path/UniformExcitation convention (sample j applies at t = j*dt).
    """
    EI = E_CONC * I
    K = EI / L ** 3 * np.array([[12.0, -6.0 * L], [-6.0 * L, 4.0 * L * L]])
    M = np.diag([m, 0.0])
    C = zeta * wn * M + (zeta / wn) * K
    n = len(accel)

    def p_of_step(k):
        ag = accel[k + 1] if k + 1 < n else 0.0
        return np.array([-m * ag, 0.0])

    return _newmark_2dof(K, M, C, p_of_step, dt, n)[:, 0]


def test_time_history_sdof_vs_independent_newmark():
    """Near-resonant (0.8 omega_n) sine ground motion, 20 forcing cycles.

    The engine's transient story displacement must match the independent
    numpy Newmark solution of the identical 2-DOF model: peak within 1%
    (observed agreement ~1e-12, asserted at 1e-6 as the 'actual' bound).
    Base shear: reactions are element resisting forces = k*u(t) here, so
    peak base shear ~= k * peak displacement (2%; damping force vanishes
    at the displacement peak).
    """
    m, L, size, zeta = 10.0, 3.0, 0.3, 0.05
    I, k, wn = _sdof_th_params(m, L, size)
    dt = 0.01
    wf = 0.8 * wn
    n = int(round(20.0 * 2.0 * math.pi / wf / dt))
    accel = [2.0 * math.sin(wf * j * dt) for j in range(n)]

    mdl = _sdof_column(m=m, L=L, size=size)
    mdl.add_th_case("TH1", "X", accel, dt, damping=zeta)
    d = _run(mdl)

    th = d["th_cases"]["TH1"]
    assert len(th["t"]) == n
    assert th["t"][0] == pytest.approx(dt, rel=1e-12)
    assert th["t"][-1] == pytest.approx(n * dt, rel=1e-12)

    u_eng = np.array(th["story_ux"]["Story1"])
    u_ref = _th_numpy_reference(accel, dt, m, L, I, zeta, wn)
    peak_eng = float(np.abs(u_eng).max())
    peak_ref = float(np.abs(u_ref).max())
    assert peak_eng == pytest.approx(peak_ref, rel=1e-2)   # required bound
    assert peak_eng == pytest.approx(peak_ref, rel=1e-6)   # actual
    # whole trajectory, not just the peak
    assert float(np.abs(u_eng - u_ref).max()) <= 1e-8 * peak_ref

    # peaks block is the exact max-abs of the reported series
    pk = th["peaks"]
    assert pk["story"]["Story1"]["ux"] == pytest.approx(peak_eng, rel=1e-12)
    assert pk["story"]["Story1"]["drift_x"] == pytest.approx(
        peak_eng / L, rel=1e-12)
    assert pk["base"]["FX"] == pytest.approx(
        float(np.abs(th["base_FX"]).max()), rel=1e-12)
    # equilibrium: peak base shear ~= k * peak displacement
    assert pk["base"]["FX"] == pytest.approx(k * peak_eng, rel=2e-2)
    # inertia-based story shear agrees with the base reaction shear
    assert pk["story"]["Story1"]["shear_x"] == pytest.approx(
        pk["base"]["FX"], rel=2e-2)
    # no excitation (and no mass) in Y
    assert float(np.abs(th["story_uy"]["Story1"]).max()) <= 1e-12
    assert pk["base"]["FY"] <= 1e-9 * pk["base"]["FX"]


def test_time_history_free_vibration_log_decrement():
    """Half-sine pulse then free vibration: successive displacement peaks
    decay by the log decrement delta = 2 pi zeta / sqrt(1 - zeta^2);
    the recovered zeta must be within 5% of the requested 0.05."""
    m, L, size, zeta = 10.0, 3.0, 0.3, 0.05
    _, k, wn = _sdof_th_params(m, L, size)
    dt = 0.01
    Tn = 2.0 * math.pi / wn
    n_pulse = 10
    n_free = int(round(12.0 * Tn / dt))
    accel = [3.0 * math.sin(math.pi * j / n_pulse) for j in range(n_pulse)]
    accel += [0.0] * n_free

    mdl = _sdof_column(m=m, L=L, size=size)
    mdl.add_th_case("PULSE", "X", accel, dt, damping=zeta)
    th = OpenSeesEngine(mdl).run_time_history("PULSE")

    u = np.array(th.story_ux["Story1"])
    # positive local maxima after the pulse
    peaks = [u[i] for i in range(n_pulse + 1, len(u) - 1)
             if u[i] > u[i - 1] and u[i] >= u[i + 1] and u[i] > 0.0]
    assert len(peaks) >= 8
    n_cyc = 6
    delta = math.log(peaks[0] / peaks[n_cyc]) / n_cyc
    zeta_est = delta / math.sqrt(4.0 * math.pi ** 2 + delta ** 2)
    assert zeta_est == pytest.approx(zeta, rel=0.05)


def test_time_history_cap_validation_and_roundtrip():
    mdl = _sdof_column()
    mdl.add_th_case("LONG", "X", [0.0] * 20001, 0.01)
    d = _run(mdl)
    assert d["th_cases"] == {}
    assert "warning" in d and "20000" in d["warning"]

    # under the cap the warning key is absent
    mdl2 = _sdof_column()
    mdl2.add_th_case("SHORT", "Y", [0.0, 1.0, 0.0, -1.0], 0.02,
                     damping=0.03, scale=1.5)
    d2 = _run(mdl2)
    assert "SHORT" in d2["th_cases"] and "warning" not in d2

    # serialisation round-trip preserves the record exactly
    back = BuildingModel.from_dict(mdl2.to_dict())
    th = back.th_cases["SHORT"]
    assert (th.direction, th.dt, th.damping, th.scale) == \
        ("Y", 0.02, 0.03, 1.5)
    assert th.accel == [0.0, 1.0, 0.0, -1.0]

    # validation
    with pytest.raises(ValueError, match="direction"):
        mdl2.add_th_case("B1", "Z", [1.0], 0.01)
    with pytest.raises(ValueError, match="dt"):
        mdl2.add_th_case("B2", "X", [1.0], 0.0)
    with pytest.raises(ValueError, match="accel"):
        mdl2.add_th_case("B3", "X", [], 0.01)
    with pytest.raises(ValueError, match="damping"):
        mdl2.add_th_case("B4", "X", [1.0], 0.01, damping=1.0)
    with pytest.raises(ValueError, match="Unknown time-history"):
        OpenSeesEngine(_sdof_column()).run_time_history("NOPE")


# --------------------------------------------------------------------------- #
# 6. column orientation angle
# --------------------------------------------------------------------------- #
def _rect_column(angle):
    """Cantilever 0.3 x 0.6 rectangular column with tip mass in x AND y."""
    mdl = _new_model("rect", [3.0])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.3, 0.6))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3), story="Story1",
                   uid="C1", angle=angle)
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, 3), mx=10.0, my=10.0))
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, 3),
                                                            fx=10.0))
    mdl.add_case("PX", {"PX": 1.0})
    mdl.num_modes = 2
    return mdl


def test_orientation_angle_swaps_sway_directions():
    """angle = 0: X sway bends about the weak axis (I22), Y sway about the
    strong axis (I33) — periods match T = 2 pi sqrt(m L^3 / 3EI) at 1e-9.
    angle = 90 swaps the two directions exactly."""
    m, L = 10.0, 3.0
    I33 = 0.3 * 0.6 ** 3 / 12.0
    I22 = 0.6 * 0.3 ** 3 / 12.0
    T = {I: 2.0 * math.pi * math.sqrt(m * L ** 3 / (3.0 * E_CONC * I))
         for I in (I22, I33)}

    def periods_by_direction(angle):
        eng = OpenSeesEngine(_rect_column(angle))
        mo = eng.run_modal()
        out = {}
        for i, p in enumerate(mo.participation):
            if p["ux"] > 0.99:
                out["X"] = mo.periods[i]
            if p["uy"] > 0.99:
                out["Y"] = mo.periods[i]
        assert set(out) == {"X", "Y"}
        r = eng.run_static("PX")
        tip = next(t for t, c in eng._asm.node_coords.items()
                   if abs(c[2] - L) < 1e-9)
        return out, r.node_disp[tip]

    t0, u0 = periods_by_direction(0.0)
    t90, u90 = periods_by_direction(90.0)

    assert t0["X"] == pytest.approx(T[I22], rel=1e-9)
    assert t0["Y"] == pytest.approx(T[I33], rel=1e-9)
    # exact swap
    assert t90["X"] == pytest.approx(t0["Y"], rel=1e-9)
    assert t90["Y"] == pytest.approx(t0["X"], rel=1e-9)
    # statics agree: deflection ratio under the same X load = I33/I22 = 4
    assert u0[0] / u90[0] == pytest.approx(I33 / I22, rel=1e-9)
    assert u0[0] == pytest.approx(10.0 * L ** 3 / (3 * E_CONC * I22),
                                  rel=1e-9)
    # X load on the rotated section produces no Y deflection (principal
    # axes still align with global at 90 degrees)
    assert abs(u90[1]) <= 1e-12 * abs(u90[0])


def test_orientation_angle_square_column_invariant():
    """A SQUARE column is orientation-invariant: any angle leaves both the
    static deflection and the periods unchanged (1e-9)."""
    def solve(angle):
        mdl = _new_model("sq", [3.0])
        mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
        mdl.add_member("column", "COL", (0, 0, 0), (0, 0, 3),
                       story="Story1", uid="C1", angle=angle)
        mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
        mdl.nodal_masses.append(NodalMass((0, 0, 3), mx=5.0))
        mdl.pattern("PX", "other").nodal_loads.append(
            NodalLoad((0, 0, 3), fx=7.0))
        mdl.add_case("PX", {"PX": 1.0})
        mdl.num_modes = 1
        eng = OpenSeesEngine(mdl)
        r = eng.run_static("PX")
        tip = next(t for t, c in eng._asm.node_coords.items()
                   if abs(c[2] - 3.0) < 1e-9)
        return r.node_disp[tip][0], eng.run_modal().periods[0]

    u0, T0 = solve(0.0)
    u37, T37 = solve(37.0)
    assert u37 == pytest.approx(u0, rel=1e-9)
    assert T37 == pytest.approx(T0, rel=1e-9)
    # angle round-trips through the dict form
    mdl = _rect_column(90.0)
    back = BuildingModel.from_dict(mdl.to_dict())
    assert back.members[0].angle == 90.0
    d = mdl.to_dict()
    d["members"][0].pop("angle")            # pre-v0.4 file
    assert BuildingModel.from_dict(d).members[0].angle == 0.0


# --------------------------------------------------------------------------- #
# 7. shell element forces
# --------------------------------------------------------------------------- #
def _wall_model(P=50.0, W=2.0, H=4.0, mesh=0.25):
    mdl = _new_model("wallF", [H])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.2))
    mdl.shells.append(ShellRegion(
        "W1", "wall", "shell", "SH",
        [(0, 0, 0), (W, 0, 0), (W, 0, H), (0, 0, H)],
        mesh_size=mesh, story="Story1"))
    pts = _mesh_points(mdl)
    top = [p for p in pts if abs(p[2] - H) < 1e-9]
    for p in pts:
        if abs(p[2]) < 1e-9:
            mdl.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
    pat = mdl.pattern("P", "other")
    for p in top:
        pat.nodal_loads.append(NodalLoad(p, fx=P / len(top)))
    mdl.add_case("P", {"P": 1.0})
    mdl.add_combo("2P", {"P": 2.0})
    mdl.add_combo("PENV", {"P": 1.0}, combo_type="envelope")
    mdl.num_modes = 0
    return mdl


def test_shell_forces_wall_section_cut_equilibrium():
    """In-plane shear across a horizontal cut = applied lateral P.

    The wall's local axes follow the corner ordering: local x runs along
    the (0,0,0)->(W,0,0) edge (horizontal), so N12 = Nxy is the in-plane
    shear flow (kN/m) and a row of quads at one height must carry
    sum(Nxy * quad width) = P (free-body of the wall above the cut).
    2% tolerance for the coarse-ish gauss-average sampling at mid-height,
    away from the clamped-base disturbance.
    """
    P, W, H = 50.0, 2.0, 4.0
    d = _run(_wall_model(P=P, W=W, H=H))
    case = d["cases"]["P"]
    quads, nodes = d["shell_quads"], d["nodes"]
    sf = case["shell_forces"]
    assert len(sf) == len(quads) > 0
    for v in sf.values():
        assert len(v) == 8 and all(math.isfinite(x) for x in v)
    # region mapping is available for every quad
    assert {q["region"] for q in quads} == {"W1"}

    def cut_shear(res, zc_target):
        tot = 0.0
        count = 0
        for qi, q in enumerate(quads):
            pts = [nodes[qi_t] for qi_t in map(str, q["nodes"])]
            zc = float(np.mean([p[2] for p in pts]))
            if abs(zc - zc_target) < 1e-6:
                wq = max(p[0] for p in pts) - min(p[0] for p in pts)
                tot += res["shell_forces"][str(qi)][2] * wq
                count += 1
        assert count == 8            # 2 m / 0.25 m mesh
        return tot

    v_mid = cut_shear(case, 2.125)   # mid-height quad-centroid row
    assert abs(v_mid) == pytest.approx(P, rel=2e-2)

    # additive combos superpose shell forces linearly
    combo = d["combos"]["2P"]
    for qi in sf:
        for i in range(8):
            assert combo["shell_forces"][qi][i] == pytest.approx(
                2.0 * sf[qi][i], abs=1e-12 + 1e-12 * abs(sf[qi][i]))
    # envelope combos do NOT report shell forces (documented v0.4)
    assert "shell_forces" not in d["combos"]["PENV"]
    # a model with no shells never grows the key
    assert "shell_forces" not in _run(_envelope_model())["cases"]["A"]


def test_shell_forces_slab_moment_section_cut():
    """One-way slab strip: cut moment sum(Mxx * width) matches statics.

    Slab L=4 x W=2 (free long edges), knife-edge supported (translations
    pinned) along x=0 and x=4, q = 5 kPa down.  Free body left of a cut at
    x: sum of Mxx across the cut = R*x - q*W*x^2/2 = q*W*x*(L-x)/2 (the
    twisting moment Mxy acts about the x axis and cannot enter this
    balance).  Sampled at the quad-centroid rows x = 1.75 and 2.25 m; 3%
    covers gauss-average-vs-midpoint parabola sampling (~0.5%) plus mesh
    error.  Signs must be uniform across the cut, and the two symmetric
    rows must agree.  Membrane forces stay identically zero (flat plate,
    transverse load: membrane and bending decouple in linear theory).
    """
    q, Lx, W = 5.0, 4.0, 2.0
    mdl = _new_model("slabF", [3.0])
    mdl.add_shell_section(ShellSection("SH", "CONC", 0.15))
    mdl.shells.append(ShellRegion(
        "S1", "slab", "shell", "SH",
        [(0, 0, 3), (Lx, 0, 3), (Lx, W, 3), (0, W, 3)],
        mesh_size=0.5, story="Story1"))
    for p in _mesh_points(mdl):
        if abs(p[0]) < 1e-9 or abs(p[0] - Lx) < 1e-9:
            mdl.supports.append(PointSupport(p, (1, 1, 1, 0, 0, 0)))
    mdl.pattern("Q", "other").area_loads.append(AreaLoad("S1", q))
    mdl.add_case("Q", {"Q": 1.0})
    mdl.num_modes = 0

    d = _run(mdl)
    case = d["cases"]["Q"]
    quads, nodes = d["shell_quads"], d["nodes"]
    # load conservation first (exact)
    assert case["base"]["FZ"] == pytest.approx(q * Lx * W, rel=1e-9)

    def cut_moment(xc_target):
        tot, vals = 0.0, []
        for qi, qd in enumerate(quads):
            pts = [nodes[t] for t in map(str, qd["nodes"])]
            xc = float(np.mean([p[0] for p in pts]))
            if abs(xc - xc_target) < 1e-6:
                wq = max(p[1] for p in pts) - min(p[1] for p in pts)
                m = case["shell_forces"][str(qi)][3]     # Mxx
                tot += m * wq
                vals.append(m)
        assert len(vals) == 4                            # W / 0.5 mesh
        return tot, vals

    for xc in (1.75, 2.25):
        tot, vals = cut_moment(xc)
        m_exact = q * W * xc * (Lx - xc) / 2.0
        assert abs(tot) == pytest.approx(m_exact, rel=3e-2)
        # sensible sign: uniform across the whole cut
        assert len({math.copysign(1.0, v) for v in vals}) == 1
    t1, _ = cut_moment(1.75)
    t2, _ = cut_moment(2.25)
    assert t1 == pytest.approx(t2, rel=1e-6)             # symmetry
    # flat plate: membrane resultants decouple and vanish
    max_membrane = max(abs(case["shell_forces"][str(i)][j])
                       for i in range(len(quads)) for j in (0, 1, 2))
    assert max_membrane <= 1e-6


# --------------------------------------------------------------------------- #
# 8. full v0.4 round trip
# --------------------------------------------------------------------------- #
def test_v04_full_roundtrip_and_api(api_client):
    """A model exercising EVERY v0.4 field survives to_dict -> from_dict ->
    to_dict unchanged, and the same dict round-trips through POST
    /api/model."""
    mdl = quick_building(name="V04", bays_x=1, bays_y=1, stories=2)
    mdl.sections["BEAM"].mod_I33 = 0.35
    mdl.sections["COL"].mod_A = 0.8
    mdl.members[0].angle = 30.0
    mdl.mass_source = {"DEAD": 1.0, "LIVE": 0.25}
    make_wind_pattern(mdl, "WX", "X", 40.0, exposure="C")
    mdl.add_case("WX", {"WX": 1.0})
    mdl.add_combo("ENV", {"EQX": 1.0, "WX": 1.0}, combo_type="envelope")
    mdl.add_th_case("TH", "X", [0.0, 0.5, -0.5, 0.0], 0.02, damping=0.04,
                    scale=2.0)
    mdl.validate()

    d1 = mdl.to_dict()
    d2 = BuildingModel.from_dict(d1).to_dict()
    assert d2 == d1

    r = api_client.post("/api/model", json=d1)
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == d1

    # the engine accepts it end to end: envelope combo + th case included
    res = OpenSeesEngine(BuildingModel.from_dict(d1)).run().to_dict()
    assert "min" in res["combos"]["ENV"]
    assert "min" not in res["combos"]["1.2D + 1.6L"]
    assert "TH" in res["th_cases"]
