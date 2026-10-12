"""Representative models for the model-exchange round-trip tests."""

from skyframe.core import builtup as _bu
from skyframe.core.model import (AreaLoad, BuildingModel, FrameSection,
                                 GridSystem, Material, MemberLoad, MemberUDL,
                                 NodalLoad, Opening, ShellSection,
                                 StoryForce, library_material)
from skyframe.core.section_ext import section_from_dims
from skyframe.core.steel_db import section_from_db


def representative_model(with_dynamic: bool = True) -> BuildingModel:
    """Two stories, 2 x 1 bays: concrete frame + steel database / parametric
    / built-up / general sections, a wall with an opening, a shell slab, a
    membrane slab, a brace, a link, a spring, releases, offsets, angles,
    cardinal points, groups, every load kind E2K expresses, static / RS /
    TH cases, combos and the mass source."""
    m = BuildingModel(name="Exchange Rep")
    m.frame_axis_convention = "etabs"
    m.add_material(Material("CONC", E=25e6, nu=0.2, unit_weight=24.0,
                            fc=30000.0))
    m.add_material(library_material("A992Fy50"))
    m.add_section(FrameSection.rectangular("COL", "CONC", 0.5, 0.5))
    m.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.6))
    m.add_section(section_from_db("aisc", "W14X90", "A992Fy50"))
    m.add_section(section_from_dims("CH1", "A992Fy50", "channel",
                                    {"d": 0.3, "bf": 0.09, "tf": 0.0135,
                                     "tw": 0.008}))
    m.add_section(FrameSection("GEN1", "A992Fy50", A=0.01, I33=2e-4,
                               I22=8e-5, J=1e-6, b=0.2, h=0.3))
    bs = _bu.template_section("plate_girder", {"d": 0.6, "tw": 0.010,
                                               "bf_top": 0.25,
                                               "tf_top": 0.02,
                                               "bf_bot": 0.30,
                                               "tf_bot": 0.025},
                              name="PG1", material="A992Fy50")
    m.add_builtup_section(bs)
    m.sections["BEAM"].mod_I33 = 0.5
    m.add_shell_section(ShellSection("WALL25", "CONC", 0.25,
                                     shell_type="shell_thick"))
    m.add_shell_section(ShellSection("SLAB20", "CONC", 0.2,
                                     shell_type="shell_thin", m11=0.25,
                                     m22=0.25))
    m.add_shell_section(ShellSection("DECK", "CONC", 0.15))
    xs, ys = [0.0, 6.0, 12.0], [0.0, 5.0]
    m.grid = GridSystem(xs, ys)
    m.grid_systems = [m.grid]
    m.set_stories([4.0, 3.5])
    m.base_fixity = "fixed"
    dead = m.pattern("DEAD", "dead")
    dead.self_weight_factor = 1.0
    live = m.pattern("LIVE", "live")
    eqx = m.pattern("EQX", "quake")
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for x in xs:
            for y in ys:
                sec = "W14X90" if x == 12.0 else "COL"
                m.add_member("column", sec, (x, y, zb), (x, y, zt),
                             story=st.name, uid=f"C{si}{x:g}{y:g}",
                             angle=30.0 if (x, y) == (6.0, 5.0) else 0.0)
        for y in ys:
            for i in range(2):
                mm = m.add_member("beam", "BEAM" if i == 0 else "PG1",
                                  (xs[i], y, zt), (xs[i + 1], y, zt),
                                  story=st.name, uid=f"BX{si}{i}{y:g}")
                dead.member_udls.append(MemberUDL(mm.uid, 20.0))
        for x in xs:
            m.add_member("beam", "CH1" if x == 6.0 else "BEAM",
                         (x, 0.0, zt), (x, 5.0, zt), story=st.name,
                         uid=f"BY{si}{x:g}",
                         releases="Mi,Mj" if x == 6.0 else "",
                         rigid_i=0.25 if x == 12.0 else 0.0,
                         rigid_j=0.25 if x == 12.0 else 0.0,
                         rigid_factor=0.5 if x == 12.0 else 1.0)
        eqx.story_forces.append(StoryForce(st.name, fx=50.0 * (si + 1)))
    m.members[-1].cardinal_point = 8
    m.add_member("brace", "GEN1", (6.0, 5.0, 0.0), (12.0, 5.0, 4.0),
                 story="Story1", uid="D1")
    # wall with a door opening between x = 0 and 6 on y = 0, story 1
    m.add_shell("wall", "shell", "WALL25",
                [(0, 0, 0), (6, 0, 0), (6, 0, 4), (0, 0, 4)],
                mesh_size=1.0, story="Story1", uid="W1",
                openings=[Opening(0.4, 0.0, 0.6, 0.5)])
    m.add_shell("slab", "shell", "SLAB20",
                [(0, 0, 4), (6, 0, 4), (6, 5, 4), (0, 5, 4)],
                mesh_size=1.5, story="Story1", uid="F1")
    m.add_shell("slab", "membrane", "DECK",
                [(6, 0, 7.5), (12, 0, 7.5), (12, 5, 7.5), (6, 5, 7.5)],
                story="Story2", uid="F2")
    dead.area_loads.append(AreaLoad("F1", 2.0))
    live.area_loads.append(AreaLoad("F2", 3.0))
    live.nodal_loads.append(NodalLoad((12.0, 5.0, 7.5), fx=5.0, fz=-12.0,
                                      mz=1.5))
    live.member_loads.append(MemberLoad("BX100", kind="point", w=15.0,
                                        a=0.4))
    live.member_loads.append(MemberLoad("BX110", kind="trapezoid", w=4.0,
                                        w2=8.0, a=0.1, b=0.9))
    live.member_loads.append(MemberLoad("BY16", kind="udl", w=3.0,
                                        direction="global_x"))
    m.add_link((12.0, 0.0, 7.5), (12.0, 5.0, 7.5),
               stiffness=[1e4, 2e4, 5e5, 0, 0, 0], uid="K1")
    m.supports = []
    m.add_spring_support((12.0, 5.0, 0.0), [0, 0, 0, 0, 0, 5e5])
    m.add_group("CORE", members=["C000", "C100"], shells=["W1"],
                links=["K1"])
    m.mass_source = {"DEAD": 1.0}
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_case("LIVE", {"LIVE": 1.0})
    m.add_case("EQX", {"EQX": 1.0})
    m.add_combo("1.2D+1.6L", {"DEAD": 1.2, "LIVE": 1.6})
    m.add_combo("ENV", {"1.2D+1.6L": 1.0, "EQX": 1.0}, combo_type="envelope")
    m.num_modes = 4
    if with_dynamic:
        m.add_spectrum_function("SPEC", [[0.0, 0.4], [0.5, 1.0],
                                         [2.0, 0.25]])
        m.add_rs_case("RSX", "X", function="SPEC", damping=0.05)
        m.add_th_function("REC", [0.0, 0.5, -0.3, 0.2, 0.0, -0.1], 0.02)
        m.add_th_case("THX", "X", function="REC", dt=0.02)
    m.validate()
    return m
