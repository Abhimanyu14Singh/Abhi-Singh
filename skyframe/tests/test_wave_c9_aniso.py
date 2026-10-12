"""Wave C (C9): anisotropic shell materials, the shell material angle,
laminates at any angle and layered shells with any layer material.
CONTRACT "Anisotropic shells, material angle, Section Designer
moment-curvature, layered shells with any material, results CSV folder".

Hand references (all closed form):

* rotation ``Qbar = T^T Q T`` - the textbook transformed reduced stiffness
  (Jones, *Mechanics of Composite Materials*, eq. 2.84):
  Qbar11 = Q11 c^4 + 2 (Q12 + 2 Q66) s^2 c^2 + Q22 s^4, ... ;
* the effective in-plane compliance of a rotated lamina
  1/Ex = c^4/E1 + (1/G12 - 2 nu12/E1) s^2 c^2 + s^4/E2 and the shear
  coupling eta_x,xy = Sbar16 / Sbar11 (Jones eq. 2.97 / 2.98);
* a uniform membrane stress state in a strip: u_x(L) = Sbar11 sigma L,
  u_y(L, 0) = Sbar16 sigma L (exact for the patch-test-passing MITC4);
* simply supported plate centre deflection - Navier series
  (:func:`skyframe.core.ortho.plate_deflection_navier`).
"""

from __future__ import annotations

import copy
import json
import math
import warnings

import numpy as np
import pytest

from skyframe.core import aniso
from skyframe.core import ortho
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, Material,
                                 NodalLoad, NodalMass, PointSupport,
                                 ShellRegion, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

TOL = 1e-6
E1, E2, NU12, G12, G13, G23 = 12.0e6, 4.0e6, 0.3, 0.7e6, 0.7e6, 0.5e6


def _run(m):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(m).run().to_dict()


def _node(res, pt):
    for tag, xyz in res["nodes"].items():
        if all(abs(a - b) < TOL for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(pt)


def _ortho_mat(name="TIM", e1=E1, e2=E2, nu=NU12, g12=G12, g13=G13,
               g23=G23, angle=None):
    return Material(name, E=e1, nu=nu, unit_weight=5.0,
                    symmetry="orthotropic", material_angle=angle,
                    ortho={"E2": e2, "G12": g12, "G13": g13, "G23": g23})


def _q0():
    return ortho.q_matrix(ortho.constants(_ortho_mat()))


def _plate(mat, sec, a=4.0, b=3.0, mesh=0.5, q=10.0, shell_type="shell"):
    m = BuildingModel(name="plate")
    m.rigid_diaphragms = False
    m.add_material(mat)
    sec.shell_type = shell_type
    m.shell_sections[sec.name] = sec
    m.set_stories([1.0])
    m.shells.append(ShellRegion("S1", "slab", "shell", sec.name,
                                [(0, 0, 1), (a, 0, 1), (a, b, 1), (0, b, 1)],
                                mesh_size=mesh, story=m.stories[0].name))
    for p in mesh_model(m).points:
        if abs(p[0]) < TOL or abs(p[0] - a) < TOL or abs(p[1]) < TOL \
                or abs(p[1] - b) < TOL:
            r = [0, 0, 1, 0, 0, 0]
            if abs(p[0]) < TOL and abs(p[1]) < TOL:
                r = [1, 1, 1, 0, 0, 1]
            elif abs(p[0] - a) < TOL and abs(p[1]) < TOL:
                r = [0, 1, 1, 0, 0, 0]
            m.supports.append(PointSupport(tuple(p), tuple(r)))
    m.pattern("Q", "dead").area_loads.append(AreaLoad("S1", q))
    m.add_case("Q", {"Q": 1.0})
    m.nodal_masses.append(NodalMass((a / 2, b / 2, 1), 1.0, 1.0, 1.0))
    m.num_modes = 1
    return m


def _w_centre(m, a=4.0, b=3.0):
    res = _run(m)
    return -res["cases"]["Q"]["node_disp"][_node(res, (a / 2, b / 2, 1))][2]


def _strip(mat, sec, L=2.0, B=1.0, mesh=0.25, sigma_t=100.0):
    """Membrane strip L x B (z = 1): u_x = 0 on x = 0, u_y = 0 at (0, 0),
    uz / rx / ry held everywhere; uniform edge traction N = sigma_t (kN/m)
    on x = L as consistent nodal forces."""
    m = BuildingModel(name="strip")
    m.rigid_diaphragms = False
    m.add_material(mat)
    m.shell_sections[sec.name] = sec
    m.set_stories([1.0])
    m.shells.append(ShellRegion("S1", "slab", "shell", sec.name,
                                [(0, 0, 1), (L, 0, 1), (L, B, 1), (0, B, 1)],
                                mesh_size=mesh, story=m.stories[0].name))
    pts = mesh_model(m).points
    edge = sorted(p for p in pts if abs(p[0] - L) < TOL)
    for p in pts:
        r = [0, 0, 1, 1, 1, 0]
        if abs(p[0]) < TOL:
            r[0] = 1
            if abs(p[1]) < TOL:
                r[1] = 1
        m.supports.append(PointSupport(tuple(p), tuple(r)))
    pat = m.pattern("N", "other")
    ys = [p[1] for p in edge]
    for i, p in enumerate(edge):
        lo = ys[i] - ys[i - 1] if i > 0 else 0.0
        hi = ys[i + 1] - ys[i] if i < len(ys) - 1 else 0.0
        pat.nodal_loads.append(NodalLoad(tuple(p), fx=sigma_t * (lo + hi)
                                         / 2.0))
    m.add_case("N", {"N": 1.0})
    m.nodal_masses.append(NodalMass((L / 2, B / 2, 1), 1.0, 1.0, 1.0))
    m.num_modes = 1
    return m


def _strip_disp(m, L=2.0):
    res = _run(m)
    d = res["cases"]["N"]["node_disp"][_node(res, (L, 0.0, 1))]
    return d[0], d[1]


# =========================================================================== #
# 1. rotation math
# =========================================================================== #
def test_rotation_90_swaps_and_45_matches_textbook():
    Q = _q0()
    Q90 = aniso.rotate_q(Q, 90.0)
    assert Q90[0, 0] == pytest.approx(Q[1, 1], rel=1e-14)
    assert Q90[1, 1] == pytest.approx(Q[0, 0], rel=1e-14)
    assert Q90[0, 1] == pytest.approx(Q[0, 1], rel=1e-14)
    assert Q90[2, 2] == pytest.approx(Q[2, 2], rel=1e-14)
    assert abs(Q90[0, 2]) < 1e-6 and abs(Q90[1, 2]) < 1e-6
    # Jones eq. 2.84 at theta = 45 deg (c = s = 1/sqrt2): c^4 = s^4 =
    # s^2c^2 = 1/4, c^3 s = c s^3 = 1/4
    q11, q22, q12, q66 = Q[0, 0], Q[1, 1], Q[0, 1], Q[2, 2]
    Qb = aniso.rotate_q(Q, 45.0)
    hand = {
        (0, 0): (q11 + 2 * (q12 + 2 * q66) + q22) / 4,
        (1, 1): (q11 + 2 * (q12 + 2 * q66) + q22) / 4,
        (0, 1): (q11 + q22 - 4 * q66) / 4 + q12 / 2,
        (2, 2): (q11 + q22 - 2 * q12 - 2 * q66) / 4 + q66 / 2,
        (0, 2): ((q11 - q12 - 2 * q66) - (q22 - q12 - 2 * q66)) / 4,
        (1, 2): ((q11 - q12 - 2 * q66) - (q22 - q12 - 2 * q66)) / 4,
    }
    for (i, j), v in hand.items():
        assert Qb[i, j] == pytest.approx(v, rel=1e-13), (i, j)
    # 1/Ex at 45 deg (Jones eq. 2.97)
    Sb = np.linalg.inv(Qb)
    inv_ex = (1 / E1 + 1 / E2 + (1 / G12 - 2 * NU12 / E1)) / 4
    assert Sb[0, 0] == pytest.approx(inv_ex, rel=1e-12)
    # transverse shear (diagonal rotation)
    assert aniso.rotate_shear(G13, G23, 90.0) == (G23, G13)
    g = aniso.rotate_shear(G13, G23, 45.0)
    assert g[0] == pytest.approx((G13 + G23) / 2) and \
        g[1] == pytest.approx((G13 + G23) / 2)


@pytest.mark.parametrize("theta", [0.0, 17.0, 45.0, 90.0, -30.0])
def test_slice_stack_is_exact(theta):
    """The 11-slice stack reproduces A, D (and B = 0) to round-off for a
    coupled target with distinct membrane / bending factors."""
    Qb = aniso.rotate_q(_q0(), theta)
    h = 0.15
    A_t = h * aniso.apply_factors(Qb, 0.7, 0.9, 0.5)
    D_t = h ** 3 / 12 * aniso.apply_factors(Qb, 0.35, 0.6, 0.25)
    sl = aniso.slices(A_t, D_t, 1.0e5, 2.0e5, h)
    assert len(sl) == 11 and sum(t for t, _ in sl) == pytest.approx(h)
    A, B, D, s13, s23 = aniso.realised(sl)
    assert np.allclose(A, A_t, rtol=0, atol=1e-9 * abs(A_t).max())
    assert np.allclose(D, D_t, rtol=0, atol=1e-9 * abs(D_t).max())
    assert abs(B).max() < 1e-9 * abs(A_t).max() * h
    assert (s13, s23) == (pytest.approx(1.0e5), pytest.approx(2.0e5))


def test_layer_stack_rank_one_and_uncoupled():
    t = 0.09
    for fib, Q in (((2.0e8, 30.0), None), (None, _q0())):
        if fib:
            m = np.array([0.75, 0.25, math.sqrt(3) / 4])
            Q = 2.0e8 * np.outer(m, m)
        sl = aniso.layer_stack(Q, 1.0, 2.0, t, fib)
        A, B, D, _, _ = aniso.realised(sl)
        assert np.allclose(A, t * Q, atol=1e-9 * abs(Q).max())
        assert np.allclose(D, t ** 3 / 12 * Q, atol=1e-12 * abs(Q).max())
        assert abs(B).max() < 1e-9 * abs(Q).max()


# =========================================================================== #
# 2. engine: material angle on an orthotropic plate
# =========================================================================== #
@pytest.mark.parametrize("shell_type", ["shell", "shell_thin",
                                        "shell_thick"])
def test_plate_rotated_90_equals_swapped_E1_E2(shell_type):
    """A material angle of 90 degrees must give EXACTLY the plate with E1
    <-> E2, G13 <-> G23 swapped (nu12' = nu21 = nu12 E2/E1)."""
    rot = _plate(_ortho_mat(angle=90.0), ShellSection("OS", "TIM", 0.2),
                 shell_type=shell_type)
    sw = _plate(_ortho_mat(e1=E2, e2=E1, nu=NU12 * E2 / E1, g13=G23,
                           g23=G13), ShellSection("OS", "TIM", 0.2),
                shell_type=shell_type)
    base = _plate(_ortho_mat(), ShellSection("OS", "TIM", 0.2),
                  shell_type=shell_type)
    w_rot, w_sw, w0 = _w_centre(rot), _w_centre(sw), _w_centre(base)
    assert w_rot == pytest.approx(w_sw, rel=1e-9)
    # the stiff axis now spans the short (3 m) direction -> stiffer plate
    assert w_rot < 0.8 * w0
    # the section-level angle overrides the material default
    sec = ShellSection("OS", "TIM", 0.2)
    sec.material_angle = 0.0
    over = _plate(_ortho_mat(angle=90.0), sec, shell_type=shell_type)
    assert _w_centre(over) == pytest.approx(w0, rel=1e-12)


def test_plate_rotated_90_navier():
    """shell_thin plate a = 4, b = 3, h = 0.06 at 90 deg vs Navier with the
    swapped D (2 %)."""
    a, b, h, q = 4.0, 3.0, 0.06, 10.0
    sec = ShellSection("OS", "TIM", h)
    sec.material_angle = 90.0
    m = _plate(_ortho_mat(), sec, a, b, a / 16, q, "shell_thin")
    D = h ** 3 / 12 * aniso.rotate_q(_q0(), 90.0)
    w_ref = ortho.plate_deflection_navier(a, b, q, D[0, 0], D[1, 1],
                                          D[0, 1] + 2 * D[2, 2])
    assert _w_centre(m, a, b) == pytest.approx(w_ref, rel=0.02)


@pytest.mark.parametrize("theta", [45.0, 30.0])
def test_membrane_strip_45_degrees_by_hand(theta):
    """Uniform tension N = 100 kN/m on a 2 x 1 strip, h = 0.1: the end
    displacement is Sbar11 N L / h and the shear-coupling slip of the
    loaded corner Sbar16 N L / h (Sbar = Qbar^-1); at 45 deg Sbar11 =
    [1/E1 + 1/E2 + 1/G12 - 2 nu12/E1] / 4 (Jones eq. 2.97)."""
    h, L, N = 0.1, 2.0, 100.0
    sec = ShellSection("OS", "TIM", h)
    sec.material_angle = theta
    ux, uy = _strip_disp(_strip(_ortho_mat(), sec, L=L, sigma_t=N), L)
    Sb = np.linalg.inv(aniso.rotate_q(_q0(), theta))
    assert ux == pytest.approx(Sb[0, 0] * N / h * L, rel=1e-6)
    assert uy == pytest.approx(Sb[0, 2] * N / h * L, rel=1e-6)
    if theta == 45.0:
        inv_ex = (1 / E1 + 1 / E2 + (1 / G12 - 2 * NU12 / E1)) / 4
        assert ux == pytest.approx(inv_ex * N / h * L, rel=1e-6)
        # 1 > 2 stiff axis at +45: tension along x shears the strip
        # toward -y at x = L (Sbar16 < 0 for E1 > E2)
        assert uy < 0.0


def test_anisotropic_material_equals_rotated_orthotropic():
    """A user Q matrix equal to the 30-degree rotated orthotropic matrix
    gives the same strip and plate response; G of a frame = Q66."""
    Qb = aniso.rotate_q(_q0(), 30.0)
    g13, g23 = aniso.rotate_shear(G13, G23, 30.0)
    an = Material("TIM", E=E1, nu=NU12, unit_weight=5.0,
                  symmetry="anisotropic",
                  aniso={"Q11": Qb[0, 0], "Q22": Qb[1, 1], "Q12": Qb[0, 1],
                         "Q16": Qb[0, 2], "Q26": Qb[1, 2], "Q66": Qb[2, 2],
                         "G13": g13, "G23": g23})
    aniso.validate_material(an)
    assert an.G == Qb[2, 2]
    sec_r = ShellSection("OS", "TIM", 0.1)
    sec_r.material_angle = 30.0
    a = _strip_disp(_strip(an, ShellSection("OS", "TIM", 0.1)))
    b = _strip_disp(_strip(_ortho_mat(), sec_r))
    assert a[0] == pytest.approx(b[0], rel=1e-9)
    assert a[1] == pytest.approx(b[1], rel=1e-9)
    wa = _w_centre(_plate(an, ShellSection("OS", "TIM", 0.2)))
    wb = _w_centre(_plate(_ortho_mat(angle=30.0),
                          ShellSection("OS", "TIM", 0.2)))
    assert wa == pytest.approx(wb, rel=1e-9)


def test_angle_45_plate_between_0_and_90():
    w = [_w_centre(_plate(_ortho_mat(angle=t), ShellSection("OS", "TIM",
                                                            0.2)))
         for t in (0.0, 45.0, 90.0)]
    assert w[2] < w[1] < w[0]


def test_laminate_any_angle_strip_by_hand():
    """Symmetric +-45 laminate [45/-45/-45/45], 4 x 0.025: A = 0.05 (Qbar45
    + Qbar-45), A16 = A26 = 0; u_x = (A^-1)_11 N L."""
    lam = {"layers": [{"t": 0.025, "material": "TIM", "angle": a}
                      for a in (45.0, -45.0, -45.0, 45.0)]}
    sec = ShellSection("LAM", "TIM", 0.1, laminate=lam)
    m = _strip(_ortho_mat(), sec, sigma_t=100.0)
    m.validate()
    A = 0.05 * (aniso.rotate_q(_q0(), 45.0) + aniso.rotate_q(_q0(), -45.0))
    assert abs(A[0, 2]) < 1e-6 * A[0, 0]
    ux, uy = _strip_disp(m)
    Ai = np.linalg.inv(A)
    assert ux == pytest.approx(Ai[0, 0] * 100.0 * 2.0, rel=1e-6)
    assert abs(uy) < 1e-9 + 1e-6 * abs(ux)
    # D16 != 0 for this laminate -> the general (coupled) path is used
    _, B, D, _ = aniso.laminate_abd(lam, {"TIM": _ortho_mat()})
    assert abs(D[0, 2]) > 1e-3 * D[0, 0] and abs(B).max() < 1e-6


# =========================================================================== #
# 3. layered shells with any material
# =========================================================================== #
def _c24():
    return ortho.preset_material("C24", "C24")


def _layered_model(builder, layers, base_mat="C24", **kw):
    mats = {"C24": _c24(),
            "CONC": Material("CONC", E=30e6, nu=0.2, unit_weight=24.0)}
    sec = ShellSection("LAY", base_mat, sum(l["t"] for l in layers),
                       layered={"layers": layers})
    m = builder(mats[base_mat], sec, **kw)
    for n, mt in mats.items():
        m.materials.setdefault(n, mt)
    return m


def test_timber_layer_membrane_stiffness_by_hand():
    """CLT as a layered section: timber 0.04 @0 / 0.04 @90 / 0.04 @0.
    A11 = 0.08 Q11 + 0.04 Q22 (C24 Q) -> u_x = (A^-1)_11 N L."""
    layers = [{"t": 0.04, "material": "C24", "kind": "timber",
               "angle": a} for a in (0.0, 90.0, 0.0)]
    m = _layered_model(_strip, layers, sigma_t=50.0)
    m.validate()
    Q = ortho.q_matrix(ortho.constants(_c24()))
    A = np.array([[0.08 * Q[0, 0] + 0.04 * Q[1, 1], 0.12 * Q[0, 1], 0.0],
                  [0.12 * Q[0, 1], 0.04 * Q[0, 0] + 0.08 * Q[1, 1], 0.0],
                  [0.0, 0.0, 0.12 * Q[2, 2]]])
    ux, uy = _strip_disp(m)
    assert ux == pytest.approx(np.linalg.inv(A)[0, 0] * 50.0 * 2.0,
                               rel=1e-6)
    assert abs(uy) < 1e-9


def test_timber_layered_plate_navier_matches_laminate():
    """The same CLT stack as a layered section bends like the Wave B
    laminate: Navier with D11 = (0.12^3 - 0.04^3)/12 Q11 + 0.04^3/12 Q22
    (continuum per layer)."""
    a, b, q = 4.0, 3.0, 2.0
    layers = [{"t": 0.04, "material": "C24", "kind": "timber",
               "angle": a_} for a_ in (0.0, 90.0, 0.0)]
    m = _layered_model(_plate, layers, a=a, b=b, mesh=a / 16, q=q,
                       shell_type="shell_thin")
    lam = ortho.clt_preset("CLT 120 L3s", "C24")
    _, _, D, _ = ortho.laminate_resultants(lam, {"C24": _c24()})
    w_ref = ortho.plate_deflection_navier(a, b, q, D[0, 0], D[1, 1],
                                          D[0, 1] + 2 * D[2, 2])
    assert _w_centre(m, a, b) == pytest.approx(w_ref, rel=0.02)


def test_mixed_concrete_timber_layered_runs_linear_and_nonlinear():
    """Concrete topping on a timber layer (unsymmetric): the linear stack
    membrane stiffness is sum t Q; a nonlinear static case builds the
    real LayeredShell with the timber layer and converges."""
    layers = [{"t": 0.10, "material": "C24", "kind": "timber", "angle": 0.0},
              {"t": 0.06, "material": "CONC", "kind": "concrete"}]
    m = _layered_model(_strip, layers, base_mat="CONC", sigma_t=40.0)
    m.validate()
    Qt = ortho.q_matrix(ortho.constants(_c24()))
    Ec, nu = 30e6, 0.2
    Qc = Ec / (1 - nu * nu) * np.array([[1, nu, 0], [nu, 1, 0],
                                        [0, 0, (1 - nu) / 2]])
    A = 0.10 * Qt + 0.06 * Qc
    ux, _ = _strip_disp(m)
    # the stack is unsymmetric: membrane-bending coupling B is restrained
    # here (rx / ry held, uz held), so u_x = (A^-1)_11 N L
    assert ux == pytest.approx(np.linalg.inv(A)[0, 0] * 40.0 * 2.0,
                               rel=1e-6)


def test_user_layer_and_validation():
    mats = {"C24": _c24(),
            "FRP": Material("FRP", E=150e6, nu=0.3, unit_weight=15.0,
                            material_type="other",
                            stress_strain={"model": "user",
                                           "hysteresis": "kinematic",
                                           "points": [[-0.01, -1.5e6],
                                                      [0.0, 0.0],
                                                      [0.01, 1.5e6]]}),
            "ISO": Material("ISO", E=70e6, nu=0.33, unit_weight=27.0,
                            material_type="aluminum")}
    la = {"t": 0.002, "material": "FRP", "kind": "user", "angle": 0.0}
    E, ang = aniso.layer_fibre(la, mats)
    assert E == pytest.approx(150e6, rel=1e-9) and ang == 0.0
    aniso.validate_layer("S", 0, la, mats)
    with pytest.raises(ValueError, match="stress_strain"):
        aniso.validate_layer("S", 0, dict(la, material="ISO"), mats)
    with pytest.raises(ValueError, match="timber"):
        aniso.validate_layer("S", 0, {"t": 0.1, "material": "ISO",
                                      "kind": "timber"}, mats)
    aniso.validate_layer("S", 0, {"t": 0.1, "material": "ISO",
                                  "kind": "elastic", "angle": 15.0}, mats)
    # FRP strips on a timber plate (membrane strip, user layer along x)
    layers = [{"t": 0.002, "material": "FRP", "kind": "user", "angle": 0.0},
              {"t": 0.10, "material": "C24", "kind": "timber",
               "angle": 0.0},
              {"t": 0.002, "material": "FRP", "kind": "user", "angle": 0.0}]
    m = _layered_model(_strip, layers, sigma_t=50.0)
    m.materials["FRP"] = mats["FRP"]
    m.validate()
    Q = ortho.q_matrix(ortho.constants(_c24()))
    A = 0.10 * Q
    A[0, 0] += 0.004 * 150e6
    ux, _ = _strip_disp(m)
    assert ux == pytest.approx(np.linalg.inv(A)[0, 0] * 100.0, rel=1e-6)


def test_mixed_layer_stresses_exact():
    """Layer stresses of a section with a new layer kind come from the real
    elastic stack: sigma_k = Q_k A^-1 N (curvature held at zero here)."""
    from skyframe.core import shell_results as sr
    layers = [{"t": 0.10, "material": "C24", "kind": "timber", "angle": 0.0},
              {"t": 0.06, "material": "CONC", "kind": "concrete"}]
    m = _layered_model(_strip, layers, base_mat="CONC", sigma_t=40.0)
    res = _run(m)
    Qt = ortho.q_matrix(ortho.constants(_c24()))
    Qc = 30e6 / 0.96 * np.array([[1, 0.2, 0], [0.2, 1, 0], [0, 0, 0.4]])
    eps = np.linalg.solve(0.10 * Qt + 0.06 * Qc, [40.0, 0.0, 0.0])
    for k, Q in ((0, Qt), (1, Qc)):
        v = sr.contour(m, res, "N", "S11", location=f"layer:{k}")["values"]
        assert v == pytest.approx([(Q @ eps)[0]] * len(v), rel=1e-6)


def _wall(layers):
    m = BuildingModel(name="tw")
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(_c24())
    c = Material("CONC", E=25e6, nu=0.2)
    c.fc = 30_000.0
    m.add_material(c)
    m.add_shell_section(ShellSection("SH", "CONC", 0.2,
                                     layered={"layers": layers}))
    m.set_stories([3.0])
    m.add_shell("wall", "shell", "SH",
                [(0, 0, 0), (2, 0, 0), (2, 0, 3), (0, 0, 3)],
                mesh_size=0.5, uid="W")
    m.pattern("P", "other").nodal_loads.append(NodalLoad((2, 0, 3),
                                                         fx=100.0))
    m.add_case("P", {"P": 1.0})
    m.add_pushover_case("PO", "X", target_drift=0.0001, steps=10)
    return m


def test_timber_core_wall_pushover_initial_stiffness():
    """Concrete / timber (30 deg) / concrete wall: the nonlinear build
    (ASDConcrete3D layers + the elastic rotated timber core realised by
    the slice stack) converges and starts at the stiffness of the same
    stack with ELASTIC concrete layers (ASDConcrete3D's first slope is
    exactly E; the membrane response does not depend on the sublayer
    split).  The aligned (0 deg) wall also matches its linear static
    stiffness (tip load = the pushover pattern for this one-story wall)."""
    tim = {"t": 0.08, "material": "C24", "kind": "timber", "angle": 30.0}
    conc = {"t": 0.06, "material": "CONC", "kind": "concrete"}
    elas = dict(conc, kind="elastic")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = _wall([conc, tim, conc])
        m.validate()
        po = OpenSeesEngine(m).run_pushover("PO")
        po_el = OpenSeesEngine(_wall([elas, tim, elas])).run_pushover("PO")
        assert len(po.roof_disp) == 10
        k0 = po.base_shear[0] / po.roof_disp[0]
        assert k0 == pytest.approx(po_el.base_shear[0] / po_el.roof_disp[0],
                                   rel=1e-4)
        tim0 = dict(tim, angle=0.0)
        eng = OpenSeesEngine(_wall([conc, tim0, conc]))
        r = eng.run_static("P")
        tip = next(t for t, c in eng._asm.struct_coords.items()
                   if abs(c[0] - 2) < 1e-9 and abs(c[2] - 3) < 1e-9)
        k_lin = 100.0 / r.node_disp[tip][0]
        po0 = OpenSeesEngine(_wall([conc, tim0, conc])).run_pushover("PO")
    assert po0.base_shear[0] / po0.roof_disp[0] == pytest.approx(k_lin,
                                                                 rel=1e-4)


# =========================================================================== #
# 4. model plumbing
# =========================================================================== #
def test_roundtrip_and_legacy_bytes():
    m = _plate(_ortho_mat(angle=20.0), ShellSection("OS", "TIM", 0.2))
    m.shell_sections["OS"].material_angle = 35.0
    m.materials["AN"] = Material("AN", E=1e7, nu=0.2, symmetry="anisotropic",
                                 aniso={"Q11": 1e7, "Q22": 5e6, "Q12": 1e6,
                                        "Q16": 1e5, "Q26": -2e5, "Q66": 2e6,
                                        "G13": 1e6, "G23": 8e5})
    m.validate()
    d = m.to_dict()
    assert d["materials"]["TIM"]["material_angle"] == 20.0
    assert d["shell_sections"]["OS"]["material_angle"] == 35.0
    assert d["materials"]["AN"]["aniso"]["Q26"] == -2e5
    m2 = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert m2.to_dict() == d
    # legacy objects never emit the new keys
    legacy = _plate(Material("C", E=30e6, nu=0.2),
                    ShellSection("OS", "C", 0.2)).to_dict()
    assert "material_angle" not in legacy["materials"]["C"]
    assert "aniso" not in legacy["materials"]["C"]
    assert "material_angle" not in legacy["shell_sections"]["OS"]
    # layer angles / new kinds survive a round trip
    lay = _layered_model(_strip, [{"t": 0.04, "material": "C24",
                                   "kind": "timber", "angle": 30.0}])
    d3 = lay.to_dict()
    assert BuildingModel.from_dict(d3).to_dict() == d3


def test_validation_messages():
    bad = Material("AN", E=1e7, nu=0.2, symmetry="anisotropic",
                   aniso={"Q11": 1e7, "Q22": 1e6, "Q12": 5e6, "Q16": 0.0,
                          "Q26": 0.0, "Q66": 1e6, "G13": 1e6, "G23": 1e6})
    with pytest.raises(ValueError, match="positive definite"):
        aniso.validate_material(bad)
    with pytest.raises(ValueError, match="aniso"):
        aniso.validate_material(Material("X", E=1.0, aniso={"Q11": 1.0}))
    m = _plate(_ortho_mat(), ShellSection("OS", "TIM", 0.2))
    m.shell_sections["OS"].material_angle = float("nan")
    with pytest.raises(ValueError, match="material_angle"):
        m.validate()
    m = _plate(_ortho_mat(), ShellSection("OS", "TIM", 0.2))
    m.shell_sections["OS"].layered = {"layers": [
        {"t": 0.1, "material": "TIM", "kind": "bogus"}]}
    with pytest.raises(ValueError, match="kind"):
        m.validate()


def test_needs_general_is_false_for_legacy_sections():
    iso = Material("C", E=30e6, nu=0.2)
    ort = _ortho_mat()
    assert not aniso.needs_general(ShellSection("S", "C", 0.2), iso, {})
    assert not aniso.needs_general(ShellSection("S", "TIM", 0.2), ort, {})
    s = ShellSection("S", "C", 0.2)
    s.material_angle = 30.0                      # isotropic: irrelevant
    assert not aniso.needs_general(s, iso, {})
    lam = ortho.clt_preset("CLT 100 L3s", "TIM")
    assert not aniso.needs_general(ShellSection("S", "TIM", 0.1,
                                                laminate=lam), ort,
                                   {"TIM": ort})
    leg = ShellSection("S", "C", 0.2, layered={"layers": [
        {"t": 0.2, "material": "C", "kind": "concrete"}]})
    assert not aniso.needs_general(leg, iso, {"C": iso})
