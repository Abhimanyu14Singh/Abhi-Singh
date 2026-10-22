"""A rigid floor's rotational mass inertia comes from where its mass arises
(found by the ETABS verification model M2: a superimposed load at the four
corners was given the inertia of a mass spread evenly over the plan, so the
torsion period came out 0.288 s instead of 0.499 s)."""

import math

import pytest

from skyframe.core.model import BuildingModel, FrameSection, Material, NodalLoad, PointSupport
from skyframe.engine.opensees_engine import OpenSeesEngine

E, h, bx, by, W = 2.0e8, 4.0, 9.0, 7.0, 300.0
I33, I22, J = 2.0e-4, 5.0e-5, 1.0e-6


def _model(named: bool, corner_loads: bool = True):
    m = BuildingModel()
    m.frame_axis_convention = "etabs"
    m.rigid_diaphragms = not named
    m.num_modes = 3
    m.add_material(Material("S", E, 0.3, 0.0))
    m.add_section(FrameSection("C", "S", 0.01, I33, I22, J))
    m.set_stories([h])
    corners = [(0.0, 0.0), (bx, 0.0), (bx, by), (0.0, by)]
    for k, (x, y) in enumerate(corners):
        m.add_member("column", "C", (x, y, 0.0), (x, y, h), uid=f"C{k}")
        m.supports.append(PointSupport((x, y, 0.0), (1,) * 6))
        if named:
            m.joint_diaphragms.append({"point": [x, y, h], "diaphragm": "D1"})
    if named:
        m.diaphragms = {"D1": {"type": "rigid"}}
    pat = m.pattern("SDL")
    if corner_loads:
        pat.nodal_loads += [NodalLoad((x, y, h), fz=-W / 4) for x, y in corners]
    m.add_case("SDL", {"SDL": 1.0})
    m.mass_source = {"SDL": 1.0}
    return m


def _torsion_period():
    G = E / 2.6
    kx, ky = 3 * E * I33 / h**3, 3 * E * I22 / h**3
    kt = 4 * (kx * (by / 2) ** 2 + ky * (bx / 2) ** 2) + 4 * G * J / h
    mass = W / 9.80665
    return 2 * math.pi * math.sqrt(mass * ((bx / 2) ** 2 + (by / 2) ** 2) / kt)


@pytest.mark.parametrize("named", [True, False])
def test_corner_mass_torsion_period(named):
    d = OpenSeesEngine(_model(named)).run().to_dict()
    periods = d["modal"]["periods"]
    tors = [p["T"] for p in d["modal"]["participation"] if p.get("rz", 0.0) > 0.5]
    assert tors, periods
    assert tors[0] == pytest.approx(_torsion_period(), rel=1e-6)
