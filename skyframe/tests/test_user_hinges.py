"""B10: user-defined plastic hinge properties + hinge overwrites.

Hand references (units kN, m, rad; E in kPa).  Cantilever column of
height L (square b = 0.3 m, E = 25 GPa, EI = E b^4 / 12), user hinge at
the base, tip load V:

* hinge moment M = V L; hinge deformation (total) theta = d_p + M / k_e
  with k_e = UH_RIGID_FACTOR * k_member, k_member = 6EI/L (M3/M2),
  EA/L (P), 12EI/L^3 (V2);
* tip displacement d = V L^3 / (3EI) + theta L (column + hinge in
  series), so the capacity curve is piecewise linear with breakpoints
  (d_X, V_X) = (M_X/L * L^3/(3EI) + (d_pX + M_X/k_e) L, M_X / L) at the
  backbone points X = B, C, D, E;
* past D the column carries the D plateau V = M_D / L; "drops" sends the
  strength to UH_RESIDUAL * M_B over the slope -UH_DROP_RATIO * k_member;
* two hinges at 0.05 L / 0.95 L of a fixed-guided member (sway): the
  moment is antisymmetric, |M| = V * 0.45 L at both hinges -> mechanism
  V = 2 My / (0.9 L);
* cyclic (material level and through a nonlinear static chain): both
  kinematic and takeda unload at k_e; kinematic re-yields at
  F_max - 2 F_y, takeda reloads from the zero-force point theta_0 on the
  straight line to the opposite yield point (-theta_y, -F_y).
"""

import json
import math

import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
OpenSeesEngine = engine_mod.OpenSeesEngine
ops = pytest.importorskip("openseespy.opensees")

from skyframe.core import user_hinges as uh  # noqa: E402
from skyframe.core.model import (BuildingModel, FrameSection,  # noqa: E402
                                 Material, NodalLoad, NodalMass,
                                 PointSupport)
from skyframe.engine import user_hinges as uhe  # noqa: E402

E = 25_000_000.0
B = 0.3
L = 3.0
EI = E * B ** 4 / 12.0
EA = E * B * B
KM = 6.0 * EI / L                          # M3 / M2 member stiffness
KE = uh.UH_RIGID_FACTOR * KM
FLEX = L ** 3 / (3.0 * EI)                 # column tip flexibility

BACKBONE = [[0, 0], [0, 1.0], [0.02, 1.2], [0.03, 0.6], [0.05, 0.6]]
ACC = {"IO": 0.005, "LS": 0.01, "CP": 0.015}
MY = 30.0


def _col(L=L, top_rot_fixed=False, mass=0.0):
    mdl = BuildingModel(name="col")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 1
    mdl.add_material(Material("C", E=E, nu=0.2))
    mdl.set_stories([L])
    mdl.add_section(FrameSection.rectangular("COL", "C", B, B))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, L), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    if top_rot_fixed:
        mdl.supports.append(PointSupport((0, 0, L), (0, 0, 0, 1, 1, 1)))
    if mass:
        mdl.nodal_masses.append(NodalMass((0, 0, L), mx=mass, my=mass))
    mdl.pattern("HY", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            fy=1.0))
    mdl.pattern("HX", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            fx=1.0))
    mdl.pattern("PZ", "other").nodal_loads.append(NodalLoad((0, 0, L),
                                                            fz=-1.0))
    return mdl


def _prop(**kw):
    p = {"type": "M3", "backbone": BACKBONE, "scale": {"yield_value": MY},
         "acceptance": ACC, "drop_strength": "holds"}
    p.update(kw)
    return p


def _assign(mdl, name, *ds, uid="C1"):
    m = next(x for x in mdl.members if x.uid == uid)
    m.hinges = [{"property": name, "relative_distance": d} for d in ds]


def _bb(pts, x):
    """Hinge force on the material envelope at deformation x (+ the
    UH_PARALLEL_RATIO elastic in parallel)."""
    return _interp(pts, x) + uh.UH_PARALLEL_RATIO * KM * x


def _interp(pts, x):
    """Piecewise-linear interpolation through [(x, y)] (flat past end)."""
    if x <= pts[0][0]:
        return pts[0][1] * x / pts[0][0] if pts[0][0] else pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _push(mdl, direction="Y", drift=0.05, steps=60):
    mdl.add_pushover_case("PO", direction, target_drift=drift, steps=steps)
    mdl.validate()
    return OpenSeesEngine(mdl).run_pushover("PO")


# =========================================================================
# data model
# =========================================================================
def test_property_normalize_and_roundtrip():
    mdl = _col()
    mdl.add_hinge_property("H", _prop(hysteresis="takeda"))
    _assign(mdl, "H", 0.05, 0.95)
    mdl.members[0].hinge_overwrites = {"auto_subdivide": True,
                                       "relative_length": 0.1}
    mdl.validate()
    d = mdl.to_dict()
    hp = d["hinge_properties"]["H"]
    assert hp["type"] == "M3" and hp["hysteresis"] == "takeda"
    assert hp["scale"] == {"yield_value": 30.0, "yield_deformation": None}
    assert hp["backbone"][2] == [0.02, 1.2]
    assert d["members"][0]["hinges"] == [
        {"property": "H", "relative_distance": 0.05},
        {"property": "H", "relative_distance": 0.95}]
    assert d["members"][0]["hinge_overwrites"] == {
        "auto_subdivide": True, "relative_length": 0.1}
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    # negative-side points may carry ETABS signs (magnitudes kept)
    p = uh.normalize_property("X", {
        "type": "P", "backbone": {"positive": [[0, 0], [0, 10]],
                                  "negative": [[0, 0], [-0.0, -20]]},
        "hysteresis": "takeda"})
    assert p["backbone"]["negative"] == [[0.0, 0.0], [0.0, 20.0]]


@pytest.mark.parametrize("mutate, msg", [
    (lambda m: _assign(m, "NOPE", 0.0), "unknown hinge property"),
    (lambda m: _assign(m, "H", 1.5), "relative_distance"),
    (lambda m: m.hinge_properties.update(
        BAD=dict(_prop(), backbone=[[0.01, 0], [0, 1]])), "first point"),
    (lambda m: m.hinge_properties.update(
        BAD=dict(_prop(), backbone=[[0, 0], [0.02, 1], [0.01, 1.1]])),
     "non-decreasing"),
    (lambda m: m.hinge_properties.update(
        BAD=dict(_prop(), type="M9")), "type must be"),
    (lambda m: m.hinge_properties.update(
        BAD=dict(_prop(), backbone={"positive": [[0, 0], [0, 1]],
                                    "negative": [[0, 0], [0, 2]]})),
     "symmetric backbone"),
    (lambda m: m.hinge_properties.update(
        BAD=dict(_prop(), acceptance={"IO": 0.02, "LS": 0.01})),
     "IO <= LS <= CP"),
    (lambda m: (m.hinge_properties.update(F={"type": "PMM_fiber"}),
                _assign(m, "F", 0.5)), "PMM_fiber hinge must sit"),
    (lambda m: (_assign(m, "H", 0.5),
                setattr(m.members[0], "rigid_i", 0.2)),
     "interior user hinges"),
    (lambda m: setattr(m.members[0], "hinge_overwrites",
                       {"relative_length": 0.9}), "relative_length"),
])
def test_validation_errors(mutate, msg):
    mdl = _col()
    mdl.add_hinge_property("H", _prop())
    _assign(mdl, "H", 0.0)
    mutate(mdl)
    with pytest.raises(ValueError, match=msg):
        mdl.validate()


def test_axial_only_member_rejects_user_hinges():
    mdl = _col()
    mdl.add_hinge_property("H", _prop())
    mdl.members[0].axial_limit = "tension"
    _assign(mdl, "H", 0.0)
    with pytest.raises(ValueError, match="axial-only"):
        mdl.validate()


# =========================================================================
# envelope conversion + materials
# =========================================================================
def test_envelope_conversion_hand():
    env = uh.build_envelope(uh.normalize_property("H", _prop()), KM)
    assert env["k_e"] == pytest.approx(KE, rel=1e-12)
    pos = env["positive"]["points"]
    hand = [(30.0 / KE, 30.0), (0.02 + 36.0 / KE, 36.0),
            (0.03 + 18.0 / KE, 18.0), (0.05 + 18.0 / KE, 18.0)]
    for (d, f), (dh, fh) in zip(pos, hand):
        assert d == pytest.approx(dh, rel=1e-12)
        assert f == pytest.approx(fh, rel=1e-12)
    # "holds": flat tail at the last force
    assert pos[-1] == pytest.approx([2.0 * hand[-1][0], 18.0])
    assert env["positive"]["markers"] == pytest.approx(
        [0.0, 0.02, 0.03, 0.05], abs=1e-15)
    # vertical drop C -> D spread over -UH_DROP_RATIO * k_member;
    # "drops" appends the drop to the residual + a flat tail
    bb = [[0, 0], [0, 1.0], [0.02, 1.2], [0.02, 0.6], [0.05, 0.6]]
    env2 = uh.build_envelope(uh.normalize_property(
        "V", _prop(backbone=bb, drop_strength="drops")), KM)
    p2 = env2["positive"]["points"]
    kd = uh.UH_DROP_RATIO * KM
    assert p2[2][0] == pytest.approx(p2[1][0] + 18.0 / kd, rel=1e-12)
    fr = uh.UH_RESIDUAL * 30.0
    assert p2[4] == pytest.approx([p2[3][0] + (18.0 - fr) / kd, fr],
                                  rel=1e-12)
    assert p2[5] == pytest.approx([2.0 * p2[4][0], fr], rel=1e-12)
    # yield_deformation "auto": deformations in multiples of F_B / k_member
    env3 = uh.build_envelope(uh.normalize_property("A", _prop(
        backbone=[[0, 0], [0, 1.0], [8.0, 1.1]],
        scale={"yield_value": MY, "yield_deformation": "auto"})), KM)
    thy = MY / KM
    assert env3["positive"]["points"][1][0] == pytest.approx(
        8.0 * thy + 33.0 / KE, rel=1e-12)


def _mat_path(prop, path):
    env = uh.build_envelope(uh.normalize_property("H", prop), KM)
    ops.wipe()
    tag, name = uhe.backbone_material(uh.normalize_property("H", prop),
                                      env, 0)
    ops.testUniaxialMaterial(tag)
    out = []
    for e in path:
        ops.setStrain(float(e))
        out.append((ops.getStress(), ops.getTangent()))
    return env, name, out


def test_material_selection():
    assert _mat_path(_prop(), [0.001])[1] == "MultiLinear"
    assert _mat_path(_prop(hysteresis="isotropic"), [0.001])[1] == \
        "MultiLinear"
    two = _prop(backbone=[[0, 0], [0, 1.0]], hysteresis="takeda")
    assert _mat_path(two, [0.001])[1] == "Hysteretic"
    assert _mat_path(_prop(hysteresis="pivot"), [0.001])[1] == \
        "HystereticSM"
    # every family reproduces the monotonic envelope exactly
    for hy in uh.HYSTERESIS_TYPES:
        env, _, out = _mat_path(_prop(hysteresis=hy),
                                [0.005, 0.015, 0.025, 0.04])
        for (f, _t), x in zip(out, [0.005, 0.015, 0.025, 0.04]):
            assert f == pytest.approx(_bb(env["positive"]["points"], x),
                                      rel=1e-9)


def test_cyclic_material_kinematic_vs_takeda():
    prop = _prop(backbone=[[0, 0], [0, 1.0], [0.05, 1.5]])
    th_y = MY / KE
    th_max = 0.01
    up = [th_max * k / 20 for k in range(1, 21)]
    down = [th_max - 1e-7] + [th_max - 0.0002 * k
                              for k in range(1, 101)]      # to -0.01
    path = up + down
    # --- kinematic (MultiLinear): unloads at k_e, re-yields at
    # F_max - 2 F_y, then the hardening slope
    env, _, out = _mat_path(prop, path)
    F_max = out[len(up) - 1][0]
    assert F_max == pytest.approx(_bb(env["positive"]["points"],
                                      th_max), rel=1e-9)
    f1 = out[len(up)][0]
    assert (F_max - f1) / 1e-7 == pytest.approx(KE, rel=1e-6)
    kh = (45.0 - 30.0) / (0.05 + 45.0 / KE - th_y)
    th_rev = th_max - 2.0 * MY / KE
    for x, (f, _t) in zip(down, out[len(up):]):
        if x < th_rev - 1e-9:
            assert f == pytest.approx(F_max - 2 * MY - kh * (th_rev - x),
                                      rel=1e-6)
    # --- takeda (peak oriented): unload at k_e to theta_0, then the
    # straight line to (-theta_y, -F_y)
    env, _, out = _mat_path(dict(prop, hysteresis="takeda"), path)
    F_max = out[len(up) - 1][0]
    th0 = th_max - F_max / KE
    kr = MY / (th0 + th_y)
    checked = 0
    for x, (f, _t) in zip(down, out[len(up):]):
        if x > th0 + 1e-12:
            assert f == pytest.approx(F_max - KE * (th_max - x), rel=1e-6,
                                      abs=1e-6)
        elif x > -th_y:
            assert f == pytest.approx(-kr * (th0 - x), rel=1e-6, abs=1e-6)
            checked += 1
    assert checked > 10


# =========================================================================
# pushover through a cantilever (M3 hinge at the base)
# =========================================================================
def _hand_curve(markers_forces, k_e=KE):
    """Capacity-curve breakpoints (d, V) for hinge points (d_p, M)."""
    pts = [(0.0, 0.0)]
    for dp, M in markers_forces:
        V = M / L
        pts.append((V * FLEX + (dp + M / k_e) * L, V))
    return pts


def test_pushover_follows_backbone_exactly():
    mdl = _col()
    mdl.add_hinge_property("H", _prop())
    _assign(mdl, "H", 0.0)
    r = _push(mdl)
    assert not r.warnings
    hd = [h for h in r.hinge_detail if h.get("user")]
    assert len(hd) == 1 and hd[0]["end"] == "i"
    assert hd[0]["material"] == "MultiLinear"
    env = uh.build_envelope(uh.normalize_property("H", _prop()), KM)
    curve = _hand_curve([(0.0, 30.0), (0.02, 36.0), (0.03, 18.0),
                         (0.05, 18.0)])
    assert curve[1] == pytest.approx((10.0 * FLEX + 30.0 / KE * L, 10.0))
    for k, (d, V) in enumerate(zip(r.roof_disp, r.base_shear)):
        # elastic column + hinge in series: theta = (d - V L^3/3EI) / L
        theta = (d - V * FLEX) / L
        assert abs(hd[0]["rot"][k]) == pytest.approx(theta, rel=1e-6,
                                                     abs=1e-10)
        assert abs(hd[0]["moment"][k]) == pytest.approx(V * L, rel=1e-6)
        # the hinge sits ON the backbone
        assert V * L == pytest.approx(
            _bb(env["positive"]["points"], theta), rel=1e-6)
        # ... so the capacity curve hits the hand breakpoints
        assert V == pytest.approx(_interp(curve, d), rel=1e-6)
    # B and C were crossed; the peak never exceeds M_C / L
    assert 11.9 < max(r.base_shear) <= 12.0 * (1 + 1e-6)
    assert r.hinge_rotations["C1"] == pytest.approx(
        abs(hd[0]["rot"][-1]), rel=1e-12)


def test_strength_drop_reproduces_D_plateau():
    mdl = _col()
    mdl.add_hinge_property("H", _prop())
    _assign(mdl, "H", 0.0)
    r = _push(mdl)
    d_D = 6.0 * FLEX + (0.03 + 18.0 / KE) * L
    past = [V for d, V in zip(r.roof_disp, r.base_shear) if d > d_D + 1e-6]
    assert len(past) > 10
    for V in past:
        assert V == pytest.approx(6.0, rel=1e-6)       # M_D / L


def test_vertical_drop_and_failure_after_E():
    bb = [[0, 0], [0, 1.0], [0.02, 1.2], [0.02, 0.6], [0.04, 0.6]]
    mdl = _col()
    mdl.add_hinge_property("H", _prop(backbone=bb, drop_strength="drops"))
    _assign(mdl, "H", 0.0)
    r = _push(mdl, drift=0.1, steps=100)
    assert not r.warnings
    env = uh.build_envelope(mdl.hinge_properties["H"], KM)
    pts = env["positive"]["points"]
    hd = [h for h in r.hinge_detail if h.get("user")][0]
    for k, (d, V) in enumerate(zip(r.roof_disp, r.base_shear)):
        theta = (d - V * FLEX) / L
        assert V * L == pytest.approx(_bb(pts, theta), rel=1e-6,
                                      abs=1e-9)
    # the D plateau (6 kN) is reached on the spread drop, then E fails
    assert any(abs(V - 6.0) < 1e-6 for V in r.base_shear)
    assert r.base_shear[-1] < 1e-4 * MY / L          # failed: ~0
    assert hd["hinge_state"][-1] == ">E"
    assert hd["state"][-1] == "collapse"


def test_acceptance_state_transitions_at_hand_steps():
    mdl = _col()
    mdl.add_hinge_property("H", _prop())
    _assign(mdl, "H", 0.0)
    r = _push(mdl)
    hd = [h for h in r.hinge_detail if h.get("user")][0]
    bands = [(0.005, "B-IO"), (0.01, "IO-LS"), (0.015, "LS-CP"),
             (0.02, ">CP"), (0.03, "C-D"), (0.05, "D-E")]
    seen = []
    for k, (d, V) in enumerate(zip(r.roof_disp, r.base_shear)):
        M = V * L
        dp = (d - V * FLEX) / L - M / KE         # hand plastic rotation
        assert hd["rot_plastic"][k] == pytest.approx(dp, abs=1e-9)
        if dp <= 1e-9:
            exp = "A-B"
        else:
            if any(abs(dp - lim) < 1e-6 for lim, _ in bands):
                continue                          # on a boundary
            exp = next((st for lim, st in bands if dp <= lim), ">E")
        assert hd["hinge_state"][k] == exp, (k, dp)
        assert hd["state"][k] == uh.LEGACY_STATE[exp]
        if exp not in seen:
            seen.append(exp)
    assert seen == ["A-B", "B-IO", "IO-LS", "LS-CP", ">CP", "C-D", "D-E"]
    # the first yielded step is the first one past the hand d_B
    d_B = 10.0 * FLEX + 30.0 / KE * L
    k_y = next(k for k, d in enumerate(r.roof_disp) if d > d_B)
    assert hd["hinge_state"][k_y - 1] == "A-B"
    assert hd["hinge_state"][k_y] == "B-IO"
    assert hd["IO"] == 0.005 and hd["My"] == MY


# =========================================================================
# multiple hinges along the member (ETABS relative distances)
# =========================================================================
def test_two_hinges_fixed_guided_member_mechanism():
    """Hinges at 0.05 L / 0.95 L of a fixed-guided (sway) member:
    V_mech = 2 My / (0.9 L)."""
    mdl = _col(top_rot_fixed=True)
    ep = _prop(backbone=[[0, 0], [0, 1.0], [0.2, 1.0]], acceptance=None)
    mdl.add_hinge_property("EPP", ep)
    _assign(mdl, "EPP", 0.05, 0.95)
    eng = OpenSeesEngine(mdl)
    mdl.add_pushover_case("PO", "Y", target_drift=0.02, steps=40)
    mdl.validate()
    r = eng.run_pushover("PO")
    assert not r.warnings
    from skyframe.core.mesh import mesh_model
    segs = mesh_model(mdl).segments["C1"]
    assert [s.x0 for s in segs] == pytest.approx([0.0, 0.15, 2.85])
    Vm = 2.0 * MY / (0.9 * L)
    assert r.base_shear[-1] == pytest.approx(Vm, rel=1e-6)
    assert max(r.base_shear) <= Vm * (1 + 1e-6)
    hd = [h for h in r.hinge_detail if h.get("user")]
    assert sorted(h["end"] for h in hd) == ["0.05", "0.95"]
    for h in hd:
        assert abs(h["moment"][-1]) == pytest.approx(MY, rel=1e-6)
        assert h["hinge_state"][-1] == "B-IO"
    # elastic branch (virtual work, m = M/V = L/2 - x):
    #   d/V = L^3/(12 EI) + 2 (0.45 L)^2 / k_e
    k_el = r.base_shear[0] / r.roof_disp[0]
    assert k_el == pytest.approx(
        1.0 / (L ** 3 / (12.0 * EI) + 2.0 * (0.45 * L) ** 2 / KE),
        rel=1e-6)


def test_interior_hinge_mid_height_cantilever():
    mdl = _col()
    mdl.add_hinge_property("EPP", _prop(
        backbone=[[0, 0], [0, 1.0], [0.2, 1.0]]))
    _assign(mdl, "EPP", 0.5)
    r = _push(mdl, drift=0.03, steps=30)
    # M at mid-height = V L / 2 -> V_y = 2 My / L
    assert r.base_shear[-1] == pytest.approx(2.0 * MY / L, rel=1e-6)


def test_linear_static_unchanged_by_interior_split():
    def tip(with_hinge):
        mdl = _col()
        mdl.add_case("LY", {"HY": 10.0})
        if with_hinge:
            mdl.add_hinge_property("H", _prop())
            _assign(mdl, "H", 0.05, 0.95)
        mdl.validate()
        res = OpenSeesEngine(mdl).run()
        nd = res.cases["LY"].node_disp
        top = max(nd, key=lambda t: res.cases["LY"].node_disp[t][1])
        return nd[top][1]
    assert tip(True) == pytest.approx(tip(False), rel=1e-9)
    assert tip(False) == pytest.approx(10.0 * FLEX, rel=1e-9)


# =========================================================================
# other hinge types
# =========================================================================
def test_m2_hinge_x_push():
    mdl = _col()
    mdl.add_hinge_property("H2", _prop(
        type="M2", backbone=[[0, 0], [0, 1.0], [0.2, 1.0]]))
    _assign(mdl, "H2", 0.0)
    r = _push(mdl, direction="X", drift=0.03, steps=30)
    assert r.base_shear[-1] == pytest.approx(MY / L, rel=1e-6)
    d_y = (MY / L) * FLEX + MY / KE * L
    assert r.base_shear[0] == pytest.approx(r.roof_disp[0] / d_y * MY / L,
                                            rel=1e-6)


def test_p_hinge_axial_cap():
    mdl = _col()
    Py = 100.0
    mdl.add_hinge_property("HP", {"type": "P", "backbone": [[0, 0], [0, 1.0],
                                                            [0.1, 1.0]],
                                  "scale": {"yield_value": Py},
                                  "drop_strength": "holds"})
    _assign(mdl, "HP", 1.0)
    mdl.add_nonlinear_static_case(
        "AX", {"PZ": 1.0}, load_application="displacement_control",
        steps=20, control_point=[0, 0, L], control_dof="UZ",
        target_disp=-0.004)
    mdl.validate()
    res = OpenSeesEngine(mdl).run_nonlinear_static("AX")
    assert res.converged
    ke = uh.UH_RIGID_FACTOR * EA / L
    flex = L / EA + 1.0 / ke
    h = res.history
    for d, fz in zip(h["disp"], h["base"]["FZ"]):
        N = fz                                   # upward reaction
        assert N <= Py * (1 + 1e-6)
        if -d < Py * flex * (1 - 1e-6):
            assert N == pytest.approx(-d / flex, rel=1e-6, abs=1e-6)
    assert h["base"]["FZ"][-1] == pytest.approx(Py, rel=1e-6)
    hd = res.hinge_detail[-1]
    assert hd["type"] == "P" and hd["end"] == "j"
    assert abs(hd["moment"][-1]) == pytest.approx(Py, rel=1e-6)


def test_v2_hinge_shear_cap():
    mdl = _col()
    Vy = 20.0
    mdl.add_hinge_property("HV", {"type": "V2", "backbone": [[0, 0], [0, 1.0],
                                                             [0.1, 1.0]],
                                  "scale": {"yield_value": Vy},
                                  "drop_strength": "holds"})
    _assign(mdl, "HV", 0.0)
    r = _push(mdl, drift=0.02, steps=20)
    ke = uh.UH_RIGID_FACTOR * 12.0 * EI / L ** 3
    assert r.base_shear[0] == pytest.approx(
        r.roof_disp[0] / (FLEX + 1.0 / ke), rel=1e-6)
    assert r.base_shear[-1] == pytest.approx(Vy, rel=1e-6)
    hd = [h for h in r.hinge_detail if h.get("user")][0]
    assert hd["type"] == "V2"
    # a shear hinge reports no rotation peak
    assert "C1" not in r.hinge_rotations


# =========================================================================
# cyclic through the structure (nonlinear static chain)
# =========================================================================
@pytest.mark.parametrize("hyst", ["kinematic", "takeda"])
def test_nls_cyclic_unloading_slopes(hyst):
    mdl = _col()
    mdl.add_hinge_property("H", _prop(
        backbone=[[0, 0], [0, 1.0], [0.05, 1.5]], hysteresis=hyst))
    _assign(mdl, "H", 0.0)
    d1 = 0.04
    mdl.add_nonlinear_static_case(
        "P1", {"HY": 1.0}, load_application="displacement_control",
        steps=20, control_point=[0, 0, L], control_dof="UY", target_disp=d1)
    mdl.add_nonlinear_static_case(
        "P2", {"HY": 1.0}, load_application="displacement_control",
        steps=80, control_point=[0, 0, L], control_dof="UY",
        target_disp=-d1, start_from="P1")
    mdl.validate()
    res = OpenSeesEngine(mdl).run_nonlinear_static("P2")
    assert res.converged
    h = res.history
    hd = [x for x in res.hinge_detail if x.get("user")][0]
    s = -1.0 if hd["rot"][0] < 0 else 1.0    # hinge sign of a +Y push
    rot = [s * v for v in hd["rot"]]
    mom = [s * v for v in hd["moment"]]
    V = [-fy for fy in h["base"]["FY"]]
    # structural unloading slope = elastic column + k_e in series
    k_sys = 1.0 / (FLEX + L ** 2 / KE)
    assert (V[0] - V[1]) / (h["disp"][0] - h["disp"][1]) == pytest.approx(
        k_sys, rel=1e-6)
    # hinge level: unloading at k_e
    assert (mom[0] - mom[1]) / (rot[0] - rot[1]) == pytest.approx(KE,
                                                                  rel=1e-6)
    th_max, F_max = rot[0], mom[0]
    th_y = MY / KE
    if hyst == "kinematic":
        th_rev = th_max - 2.0 * MY / KE
        kh = 15.0 / (0.05 + 45.0 / KE - th_y)
        n = 0
        for x, f in zip(rot, mom):
            if x < th_rev - 1e-9:
                assert f == pytest.approx(F_max - 2 * MY - kh * (th_rev - x),
                                          rel=1e-6)
                n += 1
        assert n > 10
        assert min(mom) < -MY            # yielded in the reverse direction
    else:
        th0 = th_max - F_max / KE
        kr = MY / (th0 + th_y)
        n = 0
        for x, f in zip(rot, mom):
            if -th_y < x < th0 - 1e-9:
                assert f == pytest.approx(-kr * (th0 - x), rel=1e-6)
                n += 1
        assert n > 3
        # reloading stiffness through the structure is softer than k_sys
        assert kr < KE


# =========================================================================
# nonlinear time history, auto-hinge replacement, PMM fiber, defaults
# =========================================================================
def test_nonlinear_th_reports_user_hinges():
    mdl = _col(mass=10.0)
    mdl.add_hinge_property("H", _prop(hysteresis="takeda"))
    _assign(mdl, "H", 0.0)
    acc = [6.0 * math.sin(2.0 * math.pi * k * 0.01 / 0.4)
           for k in range(150)]
    mdl.add_th_case("TH", "Y", accel=acc, dt=0.01, nonlinear=True)
    mdl.validate()
    r = OpenSeesEngine(mdl).run_time_history("TH")
    d = r.to_dict()
    hs = d["hinges"]
    assert len(hs) == 1 and hs[0]["property"] == "H"
    assert len(hs[0]["rot"]) == len(d["t"]) == 150
    assert "C1" in d["yielded"]
    assert set(hs[0]["hinge_state"]) <= set(uh.STATES)
    env = uh.build_envelope(mdl.hinge_properties["H"], KM)
    fmax = max(f for _d, f in env["positive"]["points"])
    assert max(abs(m) for m in hs[0]["moment"]) <= fmax * (1 + 1e-6)
    assert d["hinge_rotations"]["C1"] == pytest.approx(
        max(abs(x) for x in hs[0]["rot"]), rel=1e-12)


def test_user_hinges_replace_auto_hinges():
    mdl = _col()
    mdl.add_hinge_property("EPP", _prop(
        backbone=[[0, 0], [0, 1.0], [0.2, 1.0]]))
    _assign(mdl, "EPP", 0.0)
    mdl.add_pushover_case("PO", "Y", target_drift=0.03, steps=30,
                          hinges="column_base", default_My=999.0)
    mdl.validate()
    eng = OpenSeesEngine(mdl)
    r = eng.run_pushover("PO")
    asm = eng._build(hinge_case=mdl.pushover_cases["PO"])
    assert not asm.hinge_ele                # the default_My hinge is gone
    assert len(asm.user_hinges) == 1
    assert r.base_shear[-1] == pytest.approx(MY / L, rel=1e-6)
    # an EMPTY list = "no hinges" for that member (auto also replaced)
    mdl.members[0].hinges = []
    mdl.validate()
    asm = OpenSeesEngine(mdl)._build(hinge_case=mdl.pushover_cases["PO"])
    assert not asm.hinge_ele and not asm.user_hinges


def test_pmm_fiber_reference_and_relative_length():
    def run(overwrite):
        mdl = _col()
        mdl.materials["C"].fc = 30_000.0
        mdl.add_hinge_property("F", {"type": "PMM_fiber",
                                     "acceptance": {"IO": 0.004,
                                                    "LS": 0.01,
                                                    "CP": 0.02}})
        _assign(mdl, "F", 0.0, 1.0)
        if overwrite:
            mdl.members[0].hinge_overwrites = {"relative_length": 0.1}
        r = _push(mdl, drift=0.004, steps=8)
        return [h for h in r.hinge_detail if h.get("fiber")]
    fh = run(True)
    assert len(fh) == 2
    assert all(h["lp"] == pytest.approx(0.1 * L) for h in fh)
    assert fh[0]["IO"] == 0.004 and fh[0]["kind"] == "user_fiber"
    fh0 = run(False)
    assert all(h["lp"] == pytest.approx(0.5 * B) for h in fh0)


def test_defaults_byte_identical():
    def build(with_props):
        mdl = _col()
        if with_props:
            mdl.add_hinge_property("UNUSED", _prop())
        mdl.add_pushover_case("PO", "Y", target_drift=0.02, steps=10,
                              hinges="column_base", default_My=MY)
        mdl.validate()
        return mdl
    a, b = build(False), build(True)
    da, db = a.to_dict(), b.to_dict()
    assert "hinge_properties" not in da
    assert "hinge_overwrites" not in da["members"][0]
    assert da["members"][0]["hinges"] == "none"
    db.pop("hinge_properties")
    assert json.dumps(da, sort_keys=True) == json.dumps(db, sort_keys=True)
    ra = OpenSeesEngine(a).run_pushover("PO").to_dict()
    rb = OpenSeesEngine(b).run_pushover("PO").to_dict()
    assert json.dumps(ra, sort_keys=True) == json.dumps(rb, sort_keys=True)
    from skyframe.core.mesh import mesh_model
    assert len(mesh_model(a).points) == len(mesh_model(b).points) == 2
