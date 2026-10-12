"""Shared fixtures for the W8 tests (DB-edit coverage, master/similar
stories, engine-feature UI fields): one model that uses every feature the
new model tables cover."""

from skyframe.core.builder import quick_building
from skyframe.core.frequency_cases import (add_frequency_function,
                                           add_psd_case,
                                           add_steady_state_case)
from skyframe.core.model import BuildingModel, ShellSection


def w8_model() -> BuildingModel:
    m = quick_building(bays_x=2, bays_y=1, stories=3)
    s1, s2, s3 = (s.name for s in m.stories)
    m.stories[0].master = True
    m.stories[1].similar_to = s1
    z1 = m.stories[0].elevation
    # shells: shell type, one-way membrane slab, wall n1 x n2, load set
    m.add_shell_section(ShellSection("THICK", "CONC", 0.25,
                                     shell_type="shell_thick"))
    m.add_shell_section(ShellSection("DECK", "CONC", 0.15))
    m.add_shell("slab", "membrane", "DECK",
                [(0, 0, z1), (6, 0, z1), (6, 6, z1), (0, 6, z1)],
                story=s1, uid="S1")
    m.shells[-1].distribution = "one_way"
    m.shells[-1].one_way_dir = 2
    m.add_shell("wall", "shell", "THICK",
                [(12, 0, 0), (12, 6, 0), (12, 6, z1), (12, 0, z1)],
                story=s1, uid="W1")
    m.shells[-1].mesh_divisions = {"n1": 4, "n2": 3}
    m.add_shell("slab", "shell", "THICK",
                [(6, 0, z1), (12, 0, z1), (12, 6, z1), (6, 6, z1)],
                story=s1, uid="S2", mesh_size=2.0)
    m.shells[-1].mesh_divisions = {"max_size": 1.5}
    m.shells[-1].load_set = "OFFICE"
    m.shell_load_sets = {"OFFICE": {"DEAD": {"q": 1.5},
                                    "LIVE": {"q": 2.4,
                                             "direction": "global_z"}}}
    # curved beam
    m.add_member("beam", m.members[-1].section, (0, 0, z1), (0, 6, z1),
                 story=s1, uid="ARC")
    m.members[-1].curve = {"type": "arc", "via": [-1.5, 3.0, z1],
                           "segments": 8, "local2": "in_plane"}
    # named spring property + a link with params
    m.add_spring_property("FDN", {"kind": "compression_only",
                                  "k": [0, 0, 50000, 0, 0, 0]})
    m.add_spring_support((12, 6, 0), property="FDN")
    m.add_link((0, 0, 0), (0, 0, -0.5), [1e5, 1e5, 1e5, 0, 0, 0], uid="L1",
               link_type="damper", params={"cd": 250.0, "alpha": 0.5})
    # hinge property
    m.add_hinge_property("HM3", {"type": "M3",
                                 "backbone": [[0, 0], [0, 120], [0.02, 140],
                                              [0.03, 30], [0.05, 30]],
                                 "acceptance": {"IO": 0.005, "LS": 0.015,
                                                "CP": 0.02}})
    # functions
    m.add_spectrum_function("SPEC", [[0, 0.5], [1, 1.0], [2, 0.5]])
    m.add_th_function("REC", [0, 0.1, -0.2, 0.05, 0], 0.01)
    add_frequency_function(m, "FSS", "steady_state", [[0, 1.0], [10, 1.0]])
    add_frequency_function(m, "FPSD", "psd", [[0, 0.1], [10, 0.1]])
    add_steady_state_case(m, "SS1", [{"pattern": "DEAD", "scale": 2.0,
                                      "function": "FSS"}])
    add_psd_case(m, "PSD1", [{"pattern": "accel", "direction": "UX",
                              "scale": 9.80665, "function": "FPSD"}])
    # nonlinear static
    m.add_nonlinear_static_case("NLS1", {"DEAD": 1.0, "LIVE": 0.5},
                                steps=5)
    # tendon
    m.tendons = [{"uid": "T1", "points": [[0, 0, z1], [12, 0, z1]],
                  "sags": [0.2], "host": [m.members[-2].uid],
                  "pattern": "DEAD", "area": 0.001,
                  "jacking_stress": 1.2e6}]
    m.validate()
    return BuildingModel.from_dict(m.to_dict())
