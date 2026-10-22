"""A pier drawn as several wall areas is cut once per story, at its lowest
level, and its moment is taken about the whole pier's centroid (both found by
the ETABS verification model W2: stacked rows each added the story shear
again, and areas side by side lost the couple of their axial forces)."""

from skyframe.core.model import BuildingModel, Material, NodalLoad, PointSupport, ShellSection
from skyframe.engine.opensees_engine import OpenSeesEngine


def _wall(rows):
    m = BuildingModel()
    m.rigid_diaphragms = False
    m.num_modes = 0
    m.add_material(Material("C", 2.5e7, 0.2, 0.0))
    m.add_shell_section(ShellSection("W", "C", 0.3))
    m.set_stories([3.0])
    dz = 3.0 / rows
    for k in range(rows):
        for i in range(2):
            r = m.add_shell("wall", "shell", "W", [(2.0 * i, 0, k * dz), (2.0 * (i + 1), 0, k * dz),
                                                   (2.0 * (i + 1), 0, (k + 1) * dz), (2.0 * i, 0, (k + 1) * dz)],
                            uid=f"A{k}{i}")
            r.mesh_divisions = {"n1": 1, "n2": 1}
            r.pier = "P1"
    for x in (0.0, 2.0, 4.0):
        m.supports.append(PointSupport((x, 0, 0), (1,) * 6))
    for x, f in ((0.0, 0.25), (2.0, 0.5), (4.0, 0.25)):
        m.pattern("H").nodal_loads.append(NodalLoad((x, 0, 3.0), fx=100.0 * f))
    m.add_case("H", {"H": 1.0})
    return OpenSeesEngine(m).run().to_dict()["piers"]["H"]["P1"]["Story1"]


def test_stacked_regions_cut_once():
    for rows in (1, 3):
        p = _wall(rows)
        assert abs(p["V"] - 100.0) < 1e-6, (rows, p)
        assert abs(p["M"] - 300.0) < 1e-6, (rows, p)
