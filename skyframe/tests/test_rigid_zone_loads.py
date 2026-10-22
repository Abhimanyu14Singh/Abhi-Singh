"""Span loads on members with rigid end zones (found by ETABS verification F7).

Before the fix the uniform load was applied to the clear-span element only,
so the load on each rigid zone vanished (reactions summed to w*Lc, not
w*L), and station forces were read with joint-based distances against
clear-span element forces.  Now the arms carry their share to the joints,
and stations inside a zone are recovered from its arm."""

import pytest

from skyframe.core.model import BuildingModel, FrameSection, Material, MemberLoad, PointSupport
from skyframe.engine.opensees_engine import OpenSeesEngine

E, L, w, a = 2.0e8, 9.0, 20.0, 0.45          # kPa, m, kN/m, m (rigid zone at each support)
I = 1.0e-4


def _run(split):
    m = BuildingModel(name="rz")
    m.frame_axis_convention = "etabs"
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("S", E=E, nu=0.3, unit_weight=0.0))
    m.add_section(FrameSection("B", "S", A=0.01, I33=I, I22=I / 10, J=I / 20))
    m.set_stories([3.0])
    if split:
        m.add_member("beam", "B", (0, 0, 3), (L / 2, 0, 3), uid="B1", rigid_i=a, rigid_factor=1.0)
        m.add_member("beam", "B", (L / 2, 0, 3), (L, 0, 3), uid="B2", rigid_j=a, rigid_factor=1.0)
        uids = ("B1", "B2")
    else:
        m.add_member("beam", "B", (0, 0, 3), (L, 0, 3), uid="B1", rigid_i=a, rigid_j=a, rigid_factor=1.0)
        uids = ("B1",)
    for u in uids:
        m.pattern("W", "other").member_loads.append(MemberLoad(u, "udl", w, direction="gravity"))
    m.supports += [PointSupport((0, 0, 3), (1,) * 6), PointSupport((L, 0, 3), (1,) * 6)]
    m.add_case("W", {"W": 1.0})
    return OpenSeesEngine(m).run().to_dict()["cases"]["W"]


@pytest.mark.parametrize("split", [False, True])
def test_rigid_zone_load_reaches_the_supports(split):
    c = _run(split)
    assert c["base"]["FZ"] == pytest.approx(w * L, rel=1e-9)


def test_stations_face_joint_and_midspan():
    c = _run(True)
    Lc = L - 2 * a
    st1, st2 = c["member_stations"]["B1"], c["member_stations"]["B2"]
    k = min(range(len(st1["x"])), key=lambda i: abs(st1["x"][i] - a))
    assert st1["x"][k] == pytest.approx(a)
    assert abs(st1["M3"][k]) == pytest.approx(w * Lc**2 / 12, rel=1e-6)            # face
    assert abs(st1["M3"][0]) == pytest.approx(w * Lc**2 / 12 + w * Lc / 2 * a + w * a * a / 2, rel=1e-6)  # joint
    assert abs(st1["M3"][-1]) == pytest.approx(w * Lc**2 / 24, rel=1e-6)           # midspan, from B1
    assert st1["M3"][-1] == pytest.approx(st2["M3"][0], rel=1e-9)                  # continuous across the joint
