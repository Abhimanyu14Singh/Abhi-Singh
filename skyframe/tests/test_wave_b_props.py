"""Wave B properties: orthotropic materials / CLT laminates, slab property
types (ribbed / waffle / drop / deck), cold-formed shapes, composite SRC /
CFT columns, named line / area springs, one-way distribution as a section
property.  CONTRACT "Orthotropic materials, slab / deck property types,
cold-formed and composite sections, named line / area springs".

Every number below is hand-derived (shown in the docstrings) or a
published closed form:

* SS orthotropic plate centre deflection - Navier double series
  (Lekhnitskii; Timoshenko & Woinowsky-Krieger section 87):
  w = 16 q / pi^6 sum sin sin / (m n (D11 (m/a)^4 + 2H (mn/ab)^2 +
  D22 (n/b)^4)), H = D12 + 2 D66;
* deck / ribbed / waffle equivalent stiffness and weight thickness - hand
  T-section sums;
* lipped C - AISI Cold-Formed Steel Design Manual Part I linear-method
  formulas + the finite-element (section_fem) solution;
* CFT / SRC effective EI - AISC 360-16 I2.1b / I2.2b and EN 1994-1-1
  6.7.3.3 by hand; cantilever tip PL^3 / 3 EI_eff;
* multilinear area spring - settlement from the backbone inversion.
"""

from __future__ import annotations

import copy
import json
import math
import warnings

import numpy as np
import pytest

from skyframe.core import coldformed as cf
from skyframe.core import composite_col as cc
from skyframe.core import ortho
from skyframe.core import slab_props as sp
from skyframe.core import spring_dist as sd
from skyframe.core import shape_props
from skyframe.core.mesh import mesh_model
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 LineSpring, Material, NodalLoad,
                                 PointSupport, ShellRegion, ShellSection)
from skyframe.engine.opensees_engine import OpenSeesEngine

TOL = 1e-6


def _run(m):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return OpenSeesEngine(m).run().to_dict()


def _node(res, pt):
    for tag, xyz in res["nodes"].items():
        if all(abs(a - b) < TOL for a, b in zip(xyz, pt)):
            return tag
    raise AssertionError(pt)


def _plate_model(mat, sec, a, b, mesh, ss_x=True, ss_y=True, q=10.0,
                 shell_type="shell_thin"):
    """Plate a x b at z = 1, uz-restrained on the chosen edges, q down."""
    m = BuildingModel(name="plate")
    m.rigid_diaphragms = False
    m.add_material(mat)
    sec.shell_type = shell_type
    m.shell_sections[sec.name] = sec
    m.set_stories([1.0])
    z = 1.0
    m.shells.append(ShellRegion("S1", "slab", "shell", sec.name,
                                [(0, 0, z), (a, 0, z), (a, b, z), (0, b, z)],
                                mesh_size=mesh, story=m.stories[0].name))
    pts = mesh_model(m).points
    for p in pts:
        onx = abs(p[0]) < TOL or abs(p[0] - a) < TOL
        ony = abs(p[1]) < TOL or abs(p[1] - b) < TOL
        if (onx and ss_x) or (ony and ss_y):
            r = [0, 0, 1, 0, 0, 0]
            if abs(p[0]) < TOL and abs(p[1]) < TOL:
                r = [1, 1, 1, 0, 0, 1]
            elif abs(p[0] - a) < TOL and abs(p[1]) < TOL:
                r = [0, 1, 1, 0, 0, 0]
            m.supports.append(PointSupport(tuple(p), tuple(r)))
    m.pattern("Q", "dead").area_loads.append(AreaLoad("S1", q))
    m.add_case("Q", {"Q": 1.0})
    from skyframe.core.model import NodalMass
    m.nodal_masses.append(NodalMass((a / 2, b / 2, z), 1.0, 1.0, 1.0))
    m.num_modes = 1
    return m


def _ortho_mat(name="TIM", E1=12.0e6, E2=4.0e6, nu=0.3, G12=0.7e6,
               G13=0.7e6, G23=0.5e6):
    return Material(name, E=E1, nu=nu, unit_weight=5.0,
                    symmetry="orthotropic",
                    ortho={"E2": E2, "G12": G12, "G13": G13, "G23": G23})


# =========================================================================== #
# 1. orthotropic materials
# =========================================================================== #
def test_ortho_q_matrix_and_material_G():
    m = _ortho_mat()
    c = ortho.constants(m)
    Q = ortho.q_matrix(c)
    n21 = 0.3 * 4.0e6 / 12.0e6                       # 0.1
    den = 1 - 0.3 * n21                               # 0.97
    assert Q[0, 0] == pytest.approx(12.0e6 / den, rel=1e-14)
    assert Q[1, 1] == pytest.approx(4.0e6 / den, rel=1e-14)
    assert Q[0, 1] == pytest.approx(0.3 * 4.0e6 / den, rel=1e-14)
    assert Q[2, 2] == 0.7e6
    Q90 = ortho.q_matrix(c, 90)
    assert Q90[0, 0] == Q[1, 1] and Q90[1, 1] == Q[0, 0]
    assert ortho.shear_moduli(c, 90) == (0.5e6, 0.7e6)
    assert m.G == 0.7e6                               # frames use G12
    iso = Material("C", E=30e6, nu=0.2)
    assert iso.G == 30e6 / 2.4
    with pytest.raises(ValueError, match="nu12"):
        ortho.validate_material(_ortho_mat(nu=2.0))
    with pytest.raises(ValueError, match="ortho"):
        ortho.validate_material(Material("X", E=1.0, ortho={"E2": 1}))


@pytest.mark.parametrize("E2", [4.0e6, 12.0e6])
def test_orthotropic_ss_plate_navier(E2):
    """a = 4, b = 3, h = 0.06 (a/h = 67), q = 10 kPa, shell_thin (DKGQ,
    Kirchhoff) on the orthotropic skin/core LayeredShell.  Reference:
    Navier series with D = h^3/12 Q, H = D12 + 2 D66.  E2 = E1 is the
    isotropic sanity case (also equal to the legacy isotropic element)."""
    a, b, h, q = 4.0, 3.0, 0.06, 10.0
    mat = _ortho_mat(E2=E2, G12=0.7e6 if E2 < 1e7 else 12e6 / 2.6,
                     nu=0.3)
    Q = ortho.q_matrix(ortho.constants(mat))
    D = h ** 3 / 12 * Q
    w_ref = ortho.plate_deflection_navier(a, b, q, D[0, 0], D[1, 1],
                                          D[0, 1] + 2 * D[2, 2])
    m = _plate_model(mat, ShellSection("OR", "TIM", h), a, b, a / 16)
    res = _run(m)
    w = -res["cases"]["Q"]["node_disp"][_node(res, (a / 2, b / 2, 1))][2]
    assert res["cases"]["Q"]["base"]["FZ"] == pytest.approx(q * a * b,
                                                            rel=1e-9)
    assert w == pytest.approx(w_ref, rel=0.02), (w, w_ref)
    # orientation: the stiff direction 1 = local 1 = global X here, so the
    # E2 < E1 plate must be stiffer than one with the axes swapped
    if E2 < 1e7:
        w_sw = ortho.plate_deflection_navier(a, b, q, D[1, 1], D[0, 0],
                                             D[0, 1] + 2 * D[2, 2])
        assert abs(w - w_ref) < abs(w - w_sw)


def test_isotropic_material_as_orthotropic_matches_legacy():
    a, b, h = 4.0, 3.0, 0.1
    E, nu = 25e6, 0.2
    iso = Material("TIM", E=E, nu=nu, unit_weight=5.0)
    ort = Material("TIM", E=E, nu=nu, unit_weight=5.0,
                   symmetry="orthotropic",
                   ortho={"E2": E, "G12": E / 2.4, "G13": E / 2.4,
                          "G23": E / 2.4})
    w = []
    for mat in (iso, ort):
        m = _plate_model(mat, ShellSection("OR", "TIM", h), a, b, 0.5,
                         shell_type="shell_thick")
        res = _run(m)
        w.append(res["cases"]["Q"]["node_disp"][
            _node(res, (a / 2, b / 2, 1))][2])
    assert w[1] == pytest.approx(w[0], rel=1e-6)


def test_clt_laminate_resultants_by_hand():
    """C24 3-layer 40/40/40 (0/90/0): Q0 = Q(E1, E2), Q90 = swapped.
    A11 = 0.08 Q0_11 + 0.04 Q0_22;  D11 = (0.12^3 - 0.04^3)/12 Q0_11
    + 0.04^3/12 Q0_22;  S13 = 5/6 (0.08 G13 + 0.04 G23)."""
    mats = {"C24": ortho.preset_material("C24", "C24")}
    lam = ortho.clt_preset("CLT 120 L3s", "C24")
    A, B, D, (s13, s23) = ortho.laminate_resultants(lam, mats)
    Q = ortho.q_matrix(ortho.constants(mats["C24"]))
    assert A[0, 0] == pytest.approx(0.08 * Q[0, 0] + 0.04 * Q[1, 1],
                                    rel=1e-13)
    assert A[1, 1] == pytest.approx(0.04 * Q[0, 0] + 0.08 * Q[1, 1],
                                    rel=1e-13)
    d_out = (0.12 ** 3 - 0.04 ** 3) / 12
    d_in = 0.04 ** 3 / 12
    assert D[0, 0] == pytest.approx(d_out * Q[0, 0] + d_in * Q[1, 1],
                                    rel=1e-12)
    assert D[1, 1] == pytest.approx(d_out * Q[1, 1] + d_in * Q[0, 0],
                                    rel=1e-12)
    assert abs(B).max() < 1e-6 * abs(A).max()
    p = ortho.TIMBER_PRESETS["C24"]
    assert s13 == pytest.approx(5 / 6 * (0.08 * p["G13"] + 0.04 * p["G23"]))
    assert s23 == pytest.approx(5 / 6 * (0.08 * p["G23"] + 0.04 * p["G13"]))
    # unsymmetric lay-up rejected
    m = BuildingModel()
    m.add_material(mats["C24"])
    ss = ShellSection("X", "C24", 0.08,
                      laminate={"layers": [
                          {"t": 0.04, "material": "C24", "angle": 0.0},
                          {"t": 0.04, "material": "C24", "angle": 90.0}]})
    with pytest.raises(ValueError, match="symmetric"):
        ortho.validate_laminate(ss, m.materials)


def test_clt_plate_engine_vs_navier():
    """CLT 120 L3s plate 4 x 3 under 2 kPa, shell_thin: Navier with the
    laminate D (B = 0)."""
    a, b, q = 4.0, 3.0, 2.0
    mat = ortho.preset_material("C24", "C24")
    lam = ortho.clt_preset("CLT 120 L3s", "C24")
    ss = ShellSection("CLT", "C24", 0.12, laminate=lam)
    A, _, D, _ = ortho.laminate_resultants(lam, {"C24": mat})
    w_ref = ortho.plate_deflection_navier(a, b, q, D[0, 0], D[1, 1],
                                          D[0, 1] + 2 * D[2, 2])
    m = _plate_model(mat, ss, a, b, a / 16, q=q)
    m.validate()
    res = _run(m)
    w = -res["cases"]["Q"]["node_disp"][_node(res, (a / 2, b / 2, 1))][2]
    assert w == pytest.approx(w_ref, rel=0.02)


# =========================================================================== #
# 2. slab property types
# =========================================================================== #
def test_ribbed_equivalent_I_by_hand():
    """h = 0.40, hf = 0.10, stem 0.15 (prismatic), s = 0.80.
    Flange 0.80 x 0.10 (A 0.08, y 0.35), stem 0.15 x 0.30 (A 0.045,
    y 0.15): A = 0.125, ybar = (0.028 + 0.00675) / 0.125 = 0.278,
    I = 0.8*0.1^3/12 + 0.08*0.072^2 + 0.15*0.3^3/12 + 0.045*0.128^2
      = 6.6667e-5 + 4.1472e-4 + 3.375e-4 + 7.3728e-4 = 1.5561667e-3 m^4.
    D11 = E I / s; weight thickness 0.10 + 0.045/0.80 = 0.15625 m."""
    t = sp.tee_strip(0.8, 0.10, 0.15, 0.15, 0.30)
    assert t["ybar"] == pytest.approx(0.278, rel=1e-12)
    assert t["I"] == pytest.approx(1.5561666667e-3, rel=1e-9)
    E, nu = 25e6, 0.2
    mat = Material("C", E=E, nu=nu)
    slab = {"type": "ribbed", "overall_depth": 0.4, "slab_thickness": 0.1,
            "stem_width_top": 0.15, "stem_width_bottom": 0.15,
            "rib_spacing": 0.8, "rib_dir": 1}
    ss = ShellSection("R", "C", 0.15625, slab=slab)
    assert sp.weight_thickness(ss, mat) == pytest.approx(0.15625)
    A, D, S13, S23 = sp.equivalent_resultants(ss, mat)
    c = E / (1 - nu * nu)
    G = E / 2.4
    assert D[0, 0] == pytest.approx(E * 1.5561666667e-3 / 0.8, rel=1e-9)
    assert D[1, 1] == pytest.approx(c * 0.1 ** 3 / 12, rel=1e-12)
    assert D[0, 1] == pytest.approx(nu * c * 0.1 ** 3 / 12, rel=1e-12)
    assert D[2, 2] == pytest.approx(G * 0.1 ** 3 / 12, rel=1e-12)
    assert A[0, 0] == pytest.approx(c * 0.1 + E * 0.045 / 0.8, rel=1e-12)
    assert S13 == pytest.approx(5 / 6 * G * 0.15625, rel=1e-12)
    # rib_dir 2 swaps the axes
    ss2 = ShellSection("R2", "C", 0.15625, slab={**slab, "rib_dir": 2})
    A2, D2, S13b, S23b = sp.equivalent_resultants(ss2, mat)
    assert D2[1, 1] == D[0, 0] and D2[0, 0] == D[1, 1]
    assert (S13b, S23b) == (S23, S13)
    # tapered stem: trapezoid I hr^3 (wb^2 + 4 wb wt + wt^2) / 36 (wb + wt)
    a_, yc, i_ = sp._trap(0.2, 0.1, 0.3)
    assert a_ == pytest.approx(0.045)
    assert yc == pytest.approx(0.3 * (0.1 + 0.4) / (3 * 0.3))
    assert i_ == pytest.approx(0.027 * (0.01 + 0.08 + 0.04) / (36 * 0.3))


def test_waffle_weight_thickness_and_stiffness():
    """h 0.35, hf 0.08, stems 0.12, s1 = 0.9, s2 = 0.75: hr = 0.27,
    Ar = 0.0324; t_w = 0.08 + 0.0324/0.9 + 0.0324/0.75 - 0.12^2 0.27 /
    (0.9 0.75) = 0.08 + 0.036 + 0.0432 - 0.00576 = 0.15344 m."""
    mat = Material("C", E=25e6, nu=0.2)
    slab = {"type": "waffle", "overall_depth": 0.35, "slab_thickness": 0.08,
            "stem_width_top": 0.12, "stem_width_bottom": 0.12,
            "rib_spacing_1": 0.9, "rib_spacing_2": 0.75}
    ss = ShellSection("W", "C", 0.15344, slab=slab)
    assert sp.weight_thickness(ss, mat) == pytest.approx(0.15344, rel=1e-12)
    A, D, _, _ = sp.equivalent_resultants(ss, mat)
    t1 = sp.tee_strip(0.75, 0.08, 0.12, 0.12, 0.27)
    t2 = sp.tee_strip(0.9, 0.08, 0.12, 0.12, 0.27)
    assert D[0, 0] == pytest.approx(25e6 * t1["I"] / 0.75)
    assert D[1, 1] == pytest.approx(25e6 * t2["I"] / 0.9)
    assert D[0, 0] > D[1, 1]                 # closer ribs along local 1


def test_deck_equivalent_stiffness_by_hand():
    """Filled deck: tc 0.065, hr 0.05, wrt 0.15, wrb 0.11, sr 0.30,
    wd 0.10 kPa, concrete E 25e6 / nu 0.2 / gamma 24:
      Ar = (0.15 + 0.11)/2 * 0.05 = 0.0065;  A11 = c tc + E Ar/sr;
      weight thickness = 0.065 + 0.0065/0.3 + 0.10/24 = 0.0908333...
    Unfilled (steel E 200e6, gamma 77, td 0.0009, wd 0.10):
      Ld = (0.30 - 0.15) + 0.11 + 2 sqrt(0.05^2 + 0.02^2) = 0.367703...
      A11 = E td Ld / sr, A22 = E td^3 / (6 hr^2), A66 = G td,
      ybar = (0.15 * 0.05 + 2 * 0.0538516 * 0.025) / Ld,
      I = td [0.15 (0.05 - yb)^2 + 0.11 yb^2 + 2 Lw (0.05^2/12
          + (0.025 - yb)^2)]."""
    E, nu = 25e6, 0.2
    c = E / (1 - nu * nu)
    con = Material("C", E=E, nu=nu, unit_weight=24.0)
    fd = {"type": "deck_filled", "slab_depth": 0.065, "rib_depth": 0.05,
          "rib_width_top": 0.15, "rib_width_bottom": 0.11,
          "rib_spacing": 0.3, "deck_thickness": 0.0009,
          "deck_unit_weight": 0.10}
    ss = ShellSection("D", "C", 0.1, slab=fd)
    tw = 0.065 + 0.0065 / 0.3 + 0.10 / 24
    assert sp.weight_thickness(ss, con) == pytest.approx(tw, rel=1e-13)
    A, D, S13, S23 = sp.equivalent_resultants(ss, con)
    assert A[0, 0] == pytest.approx(c * 0.065 + E * 0.0065 / 0.3,
                                    rel=1e-13)
    assert A[1, 1] == pytest.approx(c * 0.065, rel=1e-13)
    t = sp.tee_strip(0.3, 0.065, 0.15, 0.11, 0.05)
    assert D[0, 0] == pytest.approx(E * t["I"] / 0.3, rel=1e-13)
    # unfilled
    st = Material("S", E=200e6, nu=0.3, unit_weight=77.0)
    ud = {k: v for k, v in fd.items() if k != "slab_depth"}
    ud["type"] = "deck_unfilled"
    su = ShellSection("U", "S", 0.1 / 77.0, slab=ud)
    lw = math.sqrt(0.05 ** 2 + 0.02 ** 2)
    Ld = 0.15 + 0.11 + 2 * lw
    yb = (0.15 * 0.05 + 2 * lw * 0.025) / Ld
    I = 0.0009 * (0.15 * (0.05 - yb) ** 2 + 0.11 * yb ** 2
                  + 2 * lw * (0.05 ** 2 / 12 + (0.025 - yb) ** 2))
    A, D, S13, S23 = sp.equivalent_resultants(su, st)
    G = 200e6 / 2.6
    assert A[0, 0] == pytest.approx(200e6 * 0.0009 * Ld / 0.3, rel=1e-13)
    assert A[1, 1] == pytest.approx(200e6 * 0.0009 ** 3 / (6 * 0.05 ** 2))
    assert A[2, 2] == pytest.approx(G * 0.0009)
    assert D[0, 0] == pytest.approx(200e6 * I / 0.3, rel=1e-12)
    assert sp.weight_thickness(su, st) == pytest.approx(0.1 / 77.0)


def test_deck_strip_engine_cylindrical_bending():
    """Filled-deck strip 4.0 x 0.6 m simply supported on its short edges
    (span along the ribs, local 1 = X), q = 5 kPa, free long edges:
    w = 5 q L^4 / (384 (D11 - D12^2 / D22)) (anticlastic plate strip)."""
    L, B, q = 4.0, 0.6, 5.0
    E, nu = 25e6, 0.2
    con = Material("C", E=E, nu=nu, unit_weight=24.0)
    fd = {"type": "deck_filled", "slab_depth": 0.065, "rib_depth": 0.05,
          "rib_width_top": 0.15, "rib_width_bottom": 0.11,
          "rib_spacing": 0.3, "deck_thickness": 0.0009,
          "deck_unit_weight": 0.10, "rib_dir": 1}
    ss = ShellSection("D", "C", 1.0, slab=fd)
    ss.thickness = sp.weight_thickness(ss, con)
    _, D, _, _ = sp.equivalent_resultants(ss, con)
    w_ref = 5 * q * L ** 4 / (384 * (D[0, 0] - D[0, 1] ** 2 / D[1, 1]))
    m = _plate_model(con, ss, L, B, 0.1, ss_x=True, ss_y=False, q=q)
    m.validate()
    res = _run(m)
    w = -res["cases"]["Q"]["node_disp"][_node(res, (L / 2, 0.3, 1))][2]
    assert w == pytest.approx(w_ref, rel=0.02)
    # swapping the rib direction makes the strip span across the ribs
    ss.slab = {**fd, "rib_dir": 2}
    res2 = _run(m)
    w2 = -res2["cases"]["Q"]["node_disp"][_node(res2, (L / 2, 0.3, 1))][2]
    _, D2, _, _ = sp.equivalent_resultants(ss, con)
    w2_ref = 5 * q * L ** 4 / (384 * (D2[0, 0] - D2[0, 1] ** 2 / D2[1, 1]))
    assert w2 == pytest.approx(w2_ref, rel=0.02)
    assert w2 > 3 * w


def test_slab_section_validation_and_thickness_sync():
    m = BuildingModel()
    m.add_material(Material("C", E=25e6, nu=0.2, unit_weight=24.0))
    slab = {"type": "ribbed", "overall_depth": 0.4, "slab_thickness": 0.1,
            "stem_width_top": 0.15, "stem_width_bottom": 0.15,
            "rib_spacing": 0.8}
    m.shell_sections["R"] = ShellSection("R", "C", 0.2, slab=slab)
    with pytest.raises(ValueError, match="weight-equivalent"):
        m.validate()
    d = m.to_dict()
    back = BuildingModel.from_dict(d)          # from_dict syncs thickness
    assert back.shell_sections["R"].thickness == pytest.approx(0.15625)
    d2 = back.to_dict()
    assert BuildingModel.from_dict(d2).to_dict() == d2
    with pytest.raises(ValueError, match="stems"):
        sp.validate_slab(ShellSection("X", "C", 0.1, slab={
            **slab, "stem_width_top": 0.9}), m.materials["C"])


# =========================================================================== #
# 3. drop panels
# =========================================================================== #
def _drop_model(with_drop=True, t_host=0.2, t_drop=0.4):
    m = BuildingModel(name="drop")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=25e6, nu=0.2, unit_weight=24.0))
    m.shell_sections["SL"] = ShellSection("SL", "C", t_host)
    m.shell_sections["DP"] = ShellSection("DP", "C", t_drop,
                                          slab={"type": "drop"})
    m.set_stories([1.0])
    z, s = 1.0, m.stories[0].name
    m.shells.append(ShellRegion("S1", "slab", "shell", "SL",
                                [(0, 0, z), (6, 0, z), (6, 6, z), (0, 6, z)],
                                mesh_size=0.5, story=s))
    if with_drop:
        m.shells.append(ShellRegion("DP1", "slab", "shell", "DP",
                                    [(2, 2, z), (4, 2, z), (4, 4, z),
                                     (2, 4, z)], mesh_size=0.5, story=s))
    for p in [(0, 0, z), (6, 0, z), (6, 6, z), (0, 6, z)]:
        m.supports.append(PointSupport(p, (1, 1, 1, 1, 1, 1)))
    pat = m.pattern("DEAD", "dead")
    pat.self_weight_factor = 1.0
    m.add_case("DEAD", {"DEAD": 1.0})
    from skyframe.core.model import NodalMass
    m.nodal_masses.append(NodalMass((3, 3, z), 1.0, 1.0, 1.0))
    m.num_modes = 1
    return m


def test_drop_panel_weight_area_and_stiffness():
    """Host 6 x 6 x 0.20, drop 2 x 2 x 0.40 (aligned with the 0.5 m mesh):
    self-weight = 24 (0.2 (36 - 4) + 0.4 * 4) = 192 kN exactly; host
    net_area 32 m^2, drop 4 m^2; the 16 overridden elements carry the
    drop section (stiffer: smaller centre deflection)."""
    m = _drop_model()
    m.validate()
    host, drop = m.shells
    assert host.net_area == pytest.approx(32.0, rel=1e-12)
    assert drop.net_area == pytest.approx(4.0, rel=1e-12)
    mesh = mesh_model(m)
    assert sum(1 for q in mesh.quads if q.region == "DP1") == 16
    assert sum(mesh.region_trib["DP1"].values()) == pytest.approx(4.0)
    assert sum(mesh.region_trib["S1"].values()) == pytest.approx(32.0)
    res = _run(m)
    assert res["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(192.0,
                                                               rel=1e-9)
    w_drop = res["cases"]["DEAD"]["node_disp"][_node(res, (3, 3, 1))][2]
    m0 = _drop_model(with_drop=False)
    m0.shell_sections["SL"].thickness = 0.2
    res0 = _run(m0)
    w0 = res0["cases"]["DEAD"]["node_disp"][_node(res0, (3, 3, 1))][2]
    assert res0["cases"]["DEAD"]["base"]["FZ"] == pytest.approx(172.8)
    assert abs(w_drop) < abs(w0)
    # weight-derived story mass counts the drop zone once
    m.mass_source = {"DEAD": 1.0}
    ms = m.compute_story_masses()
    assert sum(ms.values()) == pytest.approx(192.0 / 9.80665, rel=1e-9)
    # round trip
    d = m.to_dict()
    assert BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict() == d


def test_drop_panel_validation():
    m = _drop_model()
    m.shells[1].behavior = "membrane"
    with pytest.raises(ValueError, match="drop panel needs shell"):
        m.validate()
    m = _drop_model()
    m.shells[1].corners = [(10, 10, 1), (12, 10, 1), (12, 12, 1),
                           (10, 12, 1)]
    with pytest.raises(ValueError, match="overlaps no"):
        m.validate()


# =========================================================================== #
# 4. cold-formed shapes
# =========================================================================== #
IN = 0.0254


def test_lipped_c_vs_aisi_manual_formulas_and_fem():
    """9 x 2.5 x 0.773 in lipped C, t = 0.059, R = 0.0885 in (the AISI
    Manual Part I example geometry): the rounded-corner line integrals
    reproduce the Manual's closed-form linear-method A exactly and Ix
    within the Manual's rounded constants (0.0417, 0.637, 0.149 ->
    < 0.05 %); the finite-element outline solution agrees on A / I to
    0.1 % and on J / Cw / x0 to 2 %."""
    g = {"d": 9 * IN, "b": 2.5 * IN, "dl": 0.773 * IN, "t": 0.059 * IN,
         "r": 0.0885 * IN}
    p = shape_props.shape_properties("C_lipped", g)
    a = cf.aisi_lipped_c(9 * IN, 2.5 * IN, 0.773 * IN, 0.059 * IN,
                         0.0885 * IN)
    assert p["A"] == pytest.approx(a["A"], rel=1e-12)
    assert p["I33"] == pytest.approx(a["Ix"], rel=5e-4)
    assert p["J"] == pytest.approx(p["A"] * (0.059 * IN) ** 2 / 3,
                                   rel=1e-12)
    ex = shape_props.shape_properties("C_lipped", g, torsion="exact")
    fem = shape_props.exact_properties("C_lipped", g)
    assert fem["A"] == pytest.approx(p["A"], rel=2e-3)
    assert ex["J"] == pytest.approx(p["J"], rel=0.02)
    assert ex["x0"] == pytest.approx(p["x0"], rel=0.02)
    assert p["y0"] == 0.0 and p["I23"] == 0.0


def test_plain_channel_limit_matches_thin_walled_closed_form():
    """r = 0 lipped C with a vanishing lip -> the DG9 thin-walled channel
    shear-centre e = 3 b'^2 tf / (6 b' tf + h' tw) and Cw."""
    t, d, b = 0.004, 0.3, 0.1
    g = {"d": d, "b": b, "dl": 1e-9 + t * 1.000001, "t": t}
    p = cf.properties("C_lipped", {**g, "dl": t * 1.0000001})
    bp, hp = b - t / 2, d - t
    e = 3 * bp * bp * t / (6 * bp * t + hp * t)
    # shear centre from the web mid-line (x = t/2), negative direction
    assert (p["x0"] + p["cx"] - t / 2) == pytest.approx(-e, rel=2e-3)
    Cw = t * bp ** 3 * hp * hp / 12 * (3 * bp * t + 2 * hp * t) / (
        6 * bp * t + hp * t)
    assert p["Cw"] == pytest.approx(Cw, rel=2e-3)


def test_z_and_hat_shapes():
    z = shape_props.shape_properties("Z_lipped", {"d": 0.2, "b": 0.07,
                                                  "dl": 0.02, "t": 0.002,
                                                  "r": 0.003})
    assert z["I23"] != 0.0 and z["I_major"] > z["I33"]
    assert z["x0"] == 0.0 and z["y0"] == 0.0
    assert abs(z["cx"]) < 1e-15
    h = shape_props.shape_properties("hat", {"d": 0.1, "b": 0.08,
                                             "bf": 0.03, "t": 0.002})
    # sharp hat by hand: crown 0.078 at y 0.099, webs 2 x 0.098 at
    # mid 0.05, flanges 2 x (0.03 + 0.001) at 0.001
    L = 0.078 + 2 * 0.098 + 2 * 0.031
    yc = (0.078 * 0.099 + 2 * 0.098 * 0.05 + 2 * 0.031 * 0.001) / L
    assert h["A"] == pytest.approx(0.002 * L, rel=1e-12)
    assert h["cy"] == pytest.approx(yc, rel=1e-12)
    with pytest.raises(ValueError, match="lip"):
        shape_props.shape_properties("C_lipped", {"d": 0.2, "b": 0.07,
                                                  "dl": 0.004, "t": 0.002,
                                                  "r": 0.003})


def test_cold_formed_frame_section_from_dims():
    from skyframe.core.section_ext import section_from_dims
    sec = section_from_dims("C1", "S", "C_lipped",
                            {"d": 0.2, "b": 0.07, "dl": 0.02, "t": 0.002,
                             "r": 0.003})
    m = BuildingModel()
    m.add_material(Material("S", E=200e6, nu=0.3, unit_weight=77))
    m.sections["C1"] = sec
    d = m.to_dict()
    assert BuildingModel.from_dict(d).to_dict() == d
    # shape + dims only (no A): filled from the dims on load
    d["sections"]["C1"] = {"name": "C1", "material": "S", "shape": "hat",
                           "dims": {"d": 0.1, "b": 0.08, "bf": 0.03,
                                    "t": 0.002}}
    s2 = BuildingModel.from_dict(d).sections["C1"]
    assert s2.A == pytest.approx(shape_props.shape_properties(
        "hat", {"d": 0.1, "b": 0.08, "bf": 0.03, "t": 0.002})["A"])


# =========================================================================== #
# 5. composite columns
# =========================================================================== #
def _cc_model():
    m = BuildingModel(name="cc")
    m.rigid_diaphragms = False
    m.add_material(Material("STL", E=200e6, nu=0.3, unit_weight=77.0))
    m.add_material(Material("CON", E=30e6, nu=0.2, unit_weight=24.0))
    return m


def test_cft_effective_EI_aisc_by_hand():
    """CFT 400 x 400 x 12: As = 0.16 - 0.376^2 = 0.018624, Ag = 0.16,
    C3 = 0.45 + 3 * 0.018624 / 0.16 = 0.7992 (< 0.9);
    Is = (0.4^4 - 0.376^4)/12, Ic = 0.376^4 / 12;
    EIeff = 200e6 Is + 0.7992 * 30e6 Ic."""
    m = _cc_model()
    dims = {"d": 0.4, "b": 0.4, "t": 0.012, "concrete": "CON"}
    sec = cc.composite_section(m, "CFT", "CFT_rect", dims, "STL")
    Is = (0.4 ** 4 - 0.376 ** 4) / 12
    Ic = 0.376 ** 4 / 12
    C3 = 0.45 + 3 * 0.018624 / 0.16
    assert C3 == pytest.approx(0.7992)
    EI = 200e6 * Is + C3 * 30e6 * Ic
    assert sec.I33 * 200e6 == pytest.approx(EI, rel=1e-12)
    assert sec.I22 == sec.I33
    assert sec.A == pytest.approx((200e6 * 0.018624 + 30e6 * 0.141376)
                                  / 200e6, rel=1e-12)
    w = 77 * 0.018624 + 24 * 0.141376
    assert sec.mod_weight * 77 * sec.A == pytest.approx(w, rel=1e-12)
    # round CFT: C3 capped at 0.9 for a thick tube
    pr = cc.properties("CFT_round", {"D": 0.3, "t": 0.03,
                                     "concrete": "CON"},
                       m.materials["STL"], m.materials["CON"])
    assert pr["C"] == 0.9
    # EC4: Ke = 0.6
    pe = cc.properties("CFT_rect", {**dims, "code": "EC4"},
                       m.materials["STL"], m.materials["CON"])
    assert pe["EI33"] == pytest.approx(200e6 * Is + 0.6 * 30e6 * Ic)


def test_src_effective_EI_aisc_by_hand():
    """SRC 600 x 600 encasing W 400x200x16x10, 4 x 4 bars d 25 at 60 mm
    (12 bars).  AISC I2.1b: EIeff = Es Is + 0.5 Es Isr + C1 Ec Ic,
    C1 = 0.25 + 3 (As + Asr)/Ag <= 0.7."""
    m = _cc_model()
    dims = {"b": 0.6, "h": 0.6, "d": 0.4, "bf": 0.2, "tf": 0.016,
            "tw": 0.01, "bar_d": 0.025, "n_bars_3": 4, "n_bars_2": 4,
            "cover": 0.06, "concrete": "CON"}
    sec = cc.composite_section(m, "SRC", "SRC_I", dims, "STL")
    As = 2 * 0.2 * 0.016 + 0.368 * 0.01
    Is = (0.2 * 0.4 ** 3 - 0.19 * 0.368 ** 3) / 12
    ab = math.pi * 0.025 ** 2 / 4
    ys = [-0.24, -0.08, 0.08, 0.24]
    # bars: 4 on each of the top / bottom rows + 2 inner on each side
    bars_y = [-0.24] * 4 + [0.24] * 4 + [-0.08, 0.08] * 2
    assert len(bars_y) == 12
    Isr = sum(ab * y * y + ab * 0.025 ** 2 / 16 for y in bars_y)
    Asr = 12 * ab
    Ic = 0.6 ** 4 / 12 - Is - Isr
    C1 = min(0.25 + 3 * (As + Asr) / 0.36, 0.7)
    EI = 200e6 * Is + 0.5 * 200e6 * Isr + C1 * 30e6 * Ic
    assert sec.I33 * 200e6 == pytest.approx(EI, rel=1e-10)
    assert ys                                     # documented rows
    with pytest.raises(ValueError, match="fit"):
        cc.validate_dims("SRC_I", {**dims, "d": 0.7})


def test_cft_cantilever_tip_deflection():
    """3.0 m CFT cantilever column, lateral tip load 50 kN:
    delta = P L^3 / (3 EI_eff)."""
    m = _cc_model()
    dims = {"d": 0.4, "b": 0.4, "t": 0.012, "concrete": "CON"}
    sec = cc.composite_section(m, "CFT", "CFT_rect", dims, "STL")
    m.sections["CFT"] = sec
    m.set_stories([3.0])
    m.add_member("column", "CFT", (0, 0, 0), (0, 0, 3.0),
                 story=m.stories[0].name)
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    m.pattern("P", "other").nodal_loads.append(
        NodalLoad((0, 0, 3.0), fx=50.0))
    m.add_case("P", {"P": 1.0})
    from skyframe.core.model import NodalMass
    m.nodal_masses.append(NodalMass((0, 0, 3.0), 1.0, 1.0, 1.0))
    m.num_modes = 1
    m.validate()
    d = m.to_dict()
    assert BuildingModel.from_dict(d).to_dict() == d
    res = _run(m)
    ux = res["cases"]["P"]["node_disp"][_node(res, (0, 0, 3.0))][0]
    EI = sec.I33 * 200e6
    delta = 50 * 27 / (3 * EI)
    assert abs(ux) == pytest.approx(delta, rel=1e-6)


# =========================================================================== #
# 6. named line / area springs
# =========================================================================== #
def _spring_slab(prop=None, q=60.0):
    m = BuildingModel(name="mat")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=30e6, nu=0.2, unit_weight=0.0))
    m.shell_sections["MAT"] = ShellSection("MAT", "C", 0.8)
    m.set_stories([1.0])
    m.shells.append(ShellRegion("F1", "slab", "shell", "MAT",
                                [(0, 0, 0), (4, 0, 0), (4, 3, 0),
                                 (0, 3, 0)], mesh_size=0.5,
                                story=m.stories[0].name,
                                area_spring={"property": "SOIL"}))
    m.area_spring_properties["SOIL"] = prop or {
        "kind": "multilinear", "curves": {"U3": [[0.01, 50.0],
                                                 [0.03, 80.0]]}}
    # in-plane / rotational restraint only at one corner
    m.supports.append(PointSupport((0, 0, 0), (1, 1, 0, 0, 0, 1)))
    m.supports.append(PointSupport((4, 0, 0), (0, 1, 0, 0, 0, 0)))
    m.pattern("Q", "dead").area_loads.append(AreaLoad("F1", q))
    m.add_case("Q", {"Q": 1.0})
    from skyframe.core.model import NodalMass
    m.nodal_masses.append(NodalMass((2, 1.5, 0), 1.0, 1.0, 1.0))
    m.num_modes = 1
    return m


def test_multilinear_area_spring_backbone():
    """Backbone (per m^2) 0.01 m -> 50 kPa, 0.03 m -> 80 kPa (mirrored).
    A free-edged mat on uniform springs under uniform q settles
    uniformly; q = 60 kPa -> d = 0.01 + (60 - 50)/(80 - 50) * 0.02 =
    0.0166667 m (secant 3600 kPa/m, not the initial 5000)."""
    assert sd.backbone_settlement([[0.01, 50.0], [0.03, 80.0]], 60.0) \
        == pytest.approx(-0.01 - 0.02 / 3)
    m = _spring_slab()
    m.validate()
    res = _run(m)
    nd = res["cases"]["Q"]["node_disp"]
    for p in [(2, 1.5, 0), (0, 3, 0), (4, 3, 0), (1, 0.5, 0)]:
        assert nd[_node(res, p)][2] == pytest.approx(-0.0166667, rel=1e-4)
    assert res["cases"]["Q"]["base"]["FZ"] == pytest.approx(60 * 12,
                                                            rel=1e-6)
    # below the first point: linear 5000 kPa/m
    res2 = _run(_spring_slab(q=25.0))
    assert res2["cases"]["Q"]["node_disp"][_node(res2, (2, 1.5, 0))][2] \
        == pytest.approx(-0.005, rel=1e-5)


def test_named_linear_area_and_line_springs_match_inline():
    m1 = _spring_slab(prop={"kind": "linear", "k": [0, 0, 4000.0]}, q=20.0)
    m2 = copy.deepcopy(m1)
    m2.shells[0].area_spring = {"kz": 4000.0, "compression_only": False}
    m2.area_spring_properties = {}
    r1, r2 = _run(m1), _run(m2)
    t = _node(r1, (2, 1.5, 0))
    assert r1["cases"]["Q"]["node_disp"][t][2] == pytest.approx(
        r2["cases"]["Q"]["node_disp"][t][2], rel=1e-9)
    assert r1["cases"]["Q"]["node_disp"][t][2] == pytest.approx(-0.005)
    # line spring: named property (k per m) == inline kz
    for named in (True, False):
        m = _spring_slab(prop={"kind": "linear", "k": [0, 0, 4000.0]},
                         q=20.0)
        if named:
            m.line_spring_properties["EDGE"] = {"kind": "compression_only",
                                                "k": [0, 0, 1.0e5]}
            m.line_springs.append(LineSpring((0, 0, 0), (4, 0, 0),
                                             property="EDGE"))
        else:
            m.line_springs.append(LineSpring((0, 0, 0), (4, 0, 0),
                                             kz=1.0e5,
                                             compression_only=True))
        m.validate()
        r = _run(m)
        if named:
            wn = r["cases"]["Q"]["node_disp"][_node(r, (2, 3, 0))][2]
        else:
            wi = r["cases"]["Q"]["node_disp"][_node(r, (2, 3, 0))][2]
    assert wn == pytest.approx(wi, rel=1e-6)


def test_spring_property_validation_and_round_trip():
    m = _spring_slab()
    m.line_spring_properties["L1"] = {"kind": "linear", "k": [0, 0, 100.0]}
    m.line_springs.append(LineSpring((0, 0, 0), (4, 0, 0), property="L1"))
    d = m.to_dict()
    assert d["area_spring_properties"]["SOIL"]["kind"] == "multilinear"
    assert d["line_springs"][0]["property"] == "L1"
    assert d["shells"][0]["area_spring"] == {"property": "SOIL"}
    back = BuildingModel.from_dict(json.loads(json.dumps(d)))
    assert back.to_dict() == d
    with pytest.raises(ValueError, match="unknown area spring"):
        m.shells[0].area_spring = {"property": "NOPE"}
        m.validate()
    with pytest.raises(ValueError, match="k on U3"):
        sd.validate("X", {"kind": "compression_only", "k": [1, 0, 0]})


# =========================================================================== #
# 7. one-way distribution as a section property
# =========================================================================== #
def _membrane_bay(section_dist=None, region_dist=None, override=False):
    """4 x 8 bay (X 0..4, Y 0..8), beams on all four edges, membrane slab
    q = 5 kPa.  One-way along local 2 (= Y): the X beams (L 4) carry
    q * 8 / 2 = 20 kN/m each... (one_way_dir 1 = span X: the Y beams
    (L 8) carry q * 4 / 2 = 10 kN/m)."""
    m = BuildingModel(name="ow")
    m.rigid_diaphragms = False
    m.add_material(Material("C", E=25e6, nu=0.2, unit_weight=0.0))
    m.add_section(FrameSection.rectangular("B", "C", 0.3, 0.6))
    ss = ShellSection("SL", "C", 0.2)
    if section_dist:
        ss.distribution, ss.one_way_dir = section_dist
    m.shell_sections["SL"] = ss
    m.set_stories([3.0])
    s = m.stories[0].name
    pts = [(0, 0, 3), (4, 0, 3), (4, 8, 3), (0, 8, 3)]
    for i in range(4):
        m.add_member("beam", "B", pts[i], pts[(i + 1) % 4], story=s)
    for p in pts:
        m.add_member("column", "B", (p[0], p[1], 0), p, story=s)
    r = ShellRegion("S1", "slab", "membrane", "SL", pts, story=s)
    if region_dist:
        r.distribution, r.one_way_dir = region_dist
    r.distribution_override = override
    m.shells.append(r)
    m.pattern("Q", "dead").area_loads.append(AreaLoad("S1", 5.0))
    m.add_case("Q", {"Q": 1.0})
    return m


def _edge_loads(m):
    mesh = mesh_model(m)
    tot = {}
    for tl in mesh.membrane_loads["S1"]:
        mem = next(x for x in m.members if x.uid == tl.member_uid)
        tot[tl.member_uid] = tot.get(tl.member_uid, 0.0) + 0.5 * (
            tl.w1 + tl.w2) * (tl.b - tl.a) * mem.length * 5.0
    return tot


def test_one_way_inherited_from_section():
    """Section one_way (dir 2 = span Y) -> the two X beams (L 4) carry
    5 * 8 / 2 * 4 = 80 kN each, the Y beams nothing - identical to the
    per-slab setting; a slab with distribution_override keeps two-way."""
    inh = _edge_loads(_membrane_bay(section_dist=("one_way", 2)))
    own = _edge_loads(_membrane_bay(region_dist=("one_way", 2)))
    assert inh == pytest.approx(own, rel=1e-14)
    vals = sorted(v for v in inh.values() if v > 1e-9)
    assert vals == pytest.approx([80.0, 80.0])
    two = _edge_loads(_membrane_bay())
    ovr = _edge_loads(_membrane_bay(section_dist=("one_way", 2),
                                    override=True))
    assert ovr == pytest.approx(two, rel=1e-14)
    assert len([v for v in two.values() if v > 1e-9]) == 4
    # per-slab one-way along 1 beats the section's dir 2
    mix = _edge_loads(_membrane_bay(section_dist=("one_way", 2),
                                    region_dist=("one_way", 1)))
    assert sorted(v for v in mix.values() if v > 1e-9) == pytest.approx(
        [80.0, 80.0])
    assert mix != pytest.approx(inh)


def test_one_way_section_round_trip_and_legacy_bytes():
    m = _membrane_bay(section_dist=("one_way", 2), override=False)
    d = m.to_dict()
    assert d["shell_sections"]["SL"]["distribution"] == "one_way"
    assert d["shell_sections"]["SL"]["one_way_dir"] == 2
    assert "distribution_override" not in d["shells"][0]
    m.shells[0].distribution_override = True
    d = m.to_dict()
    assert d["shells"][0]["distribution_override"] is True
    assert BuildingModel.from_dict(json.loads(json.dumps(d))).to_dict() == d
    # legacy: none of the new keys is emitted
    leg = _membrane_bay().to_dict()
    s = json.dumps(leg)
    for k in ("distribution_override", "laminate", "\"slab\":", "ortho",
              "line_spring_properties", "area_spring_properties",
              "\"distribution\""):
        assert k not in s
    with pytest.raises(ValueError, match="distribution must be"):
        m.shell_sections["SL"].distribution = "three_way"
        m.validate()


def test_inherited_one_way_on_ineligible_slab_falls_back():
    m = _membrane_bay(section_dist=("one_way", 1))
    m.shells[0].behavior = "shell"
    assert sp.effective_distribution(m, m.shells[0])[0] == "two_way"


# =========================================================================== #
# 8. database-editing tables
# =========================================================================== #
def _full_model():
    m = _spring_slab()
    m.shells[0].corners = [tuple(float(v) for v in c)
                           for c in m.shells[0].corners]
    m.add_material(ortho.preset_material("C24", "C24"))
    m.shell_sections["CLT"] = ShellSection(
        "CLT", "C24", 0.12, laminate=ortho.clt_preset("CLT 120 L3s", "C24"))
    rib = {"type": "ribbed", "overall_depth": 0.4, "slab_thickness": 0.1,
           "stem_width_top": 0.15, "stem_width_bottom": 0.15,
           "rib_spacing": 0.8, "rib_dir": 2}
    m.shell_sections["RIB"] = ShellSection("RIB", "C", 0.15625, slab=rib,
                                           distribution="one_way",
                                           one_way_dir=2)
    m.line_spring_properties["L1"] = {"kind": "linear", "k": [0, 0, 100.0]}
    m.line_springs.append(LineSpring((0.0, 0.0, 0.0), (4.0, 0.0, 0.0),
                                     property="L1"))
    m.shells[0].distribution_override = True
    m.validate()
    # through JSON once: every coordinate becomes a float
    return BuildingModel.from_dict(json.loads(json.dumps(
        m.to_dict()))).to_dict()


def test_db_tables_round_trip_and_edit():
    from skyframe.core import modeltables as MT
    d = _full_model()
    J = json.dumps
    editable = [t.key for t in MT.catalogue() if t.editable]
    rows = {k: json.loads(J(MT.get_table(d, k)["rows"])) for k in editable}
    m, errs, _ = MT.apply_tables(d, rows)
    assert errs == [] and J(m.to_dict()) == J(d)
    keys = MT.table_keys()
    assert {"line_spring_properties", "area_spring_properties"} <= set(keys)
    # set an orthotropic E2 and a section distribution through the tables
    mr = rows["materials"]
    c24 = next(r for r in mr if r["name"] == "C24")
    assert c24["symmetry"] == "orthotropic" and c24["ortho_E2"] == 0.37e6
    c24["ortho_E2"] = 0.4e6
    sr = rows["shell_sections"]
    rib = next(r for r in sr if r["name"] == "RIB")
    assert rib["slab_type"] == "ribbed"
    assert rib["sec_distribution"] == "one_way"
    rib["sec_distribution"] = ""
    rib["sec_one_way_dir"] = None
    m, errs, _ = MT.apply_tables(d, {"materials": mr, "shell_sections": sr})
    assert errs == []
    assert m.materials["C24"].ortho["E2"] == 0.4e6
    assert m.shell_sections["RIB"].distribution is None
    assert "distribution" not in m.to_dict()["shell_sections"]["RIB"]
    # untouched legacy model: identity
    from skyframe.core.builder import quick_building
    dd = quick_building().to_dict()
    m, errs, _ = MT.apply_tables(dd, {k: json.loads(J(
        MT.get_table(dd, k)["rows"])) for k in editable})
    assert errs == [] and J(m.to_dict()) == J(dd)
