"""A small model exercising every Wave C native E2K record family:
pushover / nonlinear static / staged / steady-state / PSD cases, user and
auto hinge properties + assignments + overwrites, nonlinear link
properties and frame / shell / joint temperature loads."""

from skyframe.core.frequency_cases import (add_frequency_function,
                                           add_psd_case,
                                           add_steady_state_case)
from skyframe.core.model import (BuildingModel, FrameSection, GridSystem,
                                 Material, MemberUDL, NodalLoad,
                                 ShellSection)
from skyframe.core.thermal_ext import JointTemperature, ShellThermalLoad

H1 = {"type": "M3",
      "backbone": {"positive": [[0, 0], [0.0, 120.0], [0.02, 135.0],
                                [0.02, 30.0], [0.05, 30.0]],
                   "negative": [[0, 0], [0.0, 100.0], [0.015, 110.0],
                                [0.015, 20.0], [0.04, 20.0]]},
      "acceptance": {"IO": 0.005, "LS": 0.01, "CP": 0.015},
      "hysteresis": "takeda", "drop_strength": "drops",
      "hysteresis_params": {"pinch_x": 0.8, "pinch_y": 0.6}}
H2 = {"type": "P", "backbone": [[0, 0], [0.0, 1.0], [5.0, 1.1]],
      "scale": {"yield_value": 800.0, "yield_deformation": 0.002},
      "k_elastic": 2.0e6}
HA = {"type": "auto", "auto": {"code": "ASCE41-17", "component": "rc_beam",
                               "dof": "M3", "params": {"rho": 0.012}}}


def nonlinear_model() -> BuildingModel:
    m = BuildingModel(name="E2K Wave C")
    m.frame_axis_convention = "etabs"
    m.add_material(Material("CONC", E=25e6, nu=0.2, unit_weight=24.0,
                            fc=30000.0))
    m.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))
    m.add_section(FrameSection.rectangular("BEAM", "CONC", 0.3, 0.5))
    m.add_shell_section(ShellSection("SLAB", "CONC", 0.18,
                                     shell_type="shell_thin"))
    xs, ys = [0.0, 6.0], [0.0, 5.0]
    m.grid = GridSystem(xs, ys)
    m.grid_systems = [m.grid]
    m.set_stories([3.0, 3.0])
    dead = m.pattern("DEAD", "dead")
    dead.self_weight_factor = 1.0
    live = m.pattern("LIVE", "live")
    push = m.pattern("PUSH", "quake")
    temp = m.pattern("TEMP", "other")
    cols, beams = {}, {}
    for si, st in enumerate(m.stories):
        zt, zb = st.elevation, st.elevation - st.height
        for x in xs:
            for y in ys:
                c = m.add_member("column", "COL", (x, y, zb), (x, y, zt),
                                 story=st.name, uid=f"C{si}{x:g}{y:g}")
                cols[(si, x, y)] = c
        for y in ys:
            b = m.add_member("beam", "BEAM", (0.0, y, zt), (6.0, y, zt),
                             story=st.name, uid=f"BX{si}{y:g}")
            beams[(si, y)] = b
            dead.member_udls.append(MemberUDL(b.uid, 15.0))
        for x in xs:
            m.add_member("beam", "BEAM", (x, 0.0, zt), (x, 5.0, zt),
                         story=st.name, uid=f"BY{si}{x:g}")
        push.nodal_loads.append(NodalLoad((0.0, 0.0, zt),
                                          fx=10.0 * (si + 1),
                                          fy=8.0 * (si + 1)))
    m.add_shell("slab", "shell", "SLAB",
                [(0, 0, 3), (6, 0, 3), (6, 5, 3), (0, 5, 3)],
                mesh_size=3.0, story="Story1", uid="F1")
    live.nodal_loads.append(NodalLoad((6.0, 5.0, 6.0), fz=-20.0))
    # temperature: frame dT + gradients, shell dT + gradient, joints
    m.add_thermal_load("TEMP", "BX10", 25.0, grad2=4.0, grad3=-6.0)
    m.add_thermal_load("TEMP", "BY10", -10.0)
    temp.shell_thermal_loads.append(ShellThermalLoad("F1", 12.0, 30.0))
    temp.joint_temperatures.append(JointTemperature((6.0, 5.0, 6.0), 8.0))
    # hinges
    m.add_hinge_property("H1", H1)
    m.add_hinge_property("H2", H2)
    m.add_hinge_property("HA", HA)
    beams[(1, 0.0)].hinges = [{"property": "H1", "relative_distance": 0.0},
                              {"property": "H1", "relative_distance": 1.0}]
    beams[(0, 5.0)].hinges = [{"property": "HA", "relative_distance": 0.0},
                              {"property": "HA", "relative_distance": 1.0}]
    beams[(0, 5.0)].hinge_overwrites = {"auto_subdivide": True,
                                        "relative_length": 0.05}
    for key in ((0, 0.0, 0.0), (0, 6.0, 0.0)):
        cols[key].hinges = "auto_m3"
    # nonlinear links (detached device links: parameter round trip)
    dev = [("DMP", "damper", {"cd": 50.0, "alpha": 0.5, "k": 2.0e5}),
           ("GAP", "gap", {"k": 1.0e5, "gap": 0.001}),
           ("HOOK", "hook", {"k": 1.0e5, "slack": 0.002}),
           ("ISO", "isolator", {"k1": 2.0e4, "k2": 1.5e3, "Fy": 60.0,
                                "kv": 2.0e6}),
           ("FP", "fp_isolator", {"R": 2.0, "mu": 0.05, "k_init": 5.0e4}),
           ("ML", "multilinear", {"points": [[0.01, 50.0], [0.05, 120.0]]}),
           ("TK", "multilinear_takeda",
            {"points": [[0.01, 40.0], [0.04, 90.0]],
             "points_neg": [[-0.01, -30.0], [-0.03, -70.0]]}),
           ("MK", "multilinear_kinematic",
            {"points": [[0.01, 40.0], [0.04, 90.0]], "kv": 3.0e6}),
           ("PV", "multilinear_pivot",
            {"points": [[0.01, 40.0], [0.04, 90.0]], "pinch_x": 0.4,
             "pinch_y": 0.3}),
           ("TFP", "triple_fp", {"R1": 0.5, "R2": 2.0, "R3": 2.0,
                                 "mu1": 0.02, "mu2": 0.05, "mu3": 0.06,
                                 "d1": 0.05, "d2": 0.3, "d3": 0.3,
                                 "W": 500.0}),
           ("TC", "tc_fp_isolator", {"R": 2.5, "mu": 0.04, "kt": 1.0e3}),
           ("FS", "friction_spring", {"mu": 0.1, "k_init": 4.0e4}),
           ("BW", "rubber_isolator_bouc_wen", {"k_init": 3.0e4, "qd": 40.0,
                                               "alpha1": 0.1}),
           ("DB", "damper_bilinear", {"cd": 80.0, "Fr": 30.0,
                                      "ratio": 0.1}),
           ("WEN", "plastic_wen", {"k": 5.0e4, "Fy": 80.0, "ratio": 0.05,
                                   "exp": 2.0}),
           ("BRB", "brb", {"k": 8.0e4, "Fy": 300.0, "d_bm": 0.05})]
    for i, (uid, typ, par) in enumerate(dev):
        x = 20.0 + 3.0 * i
        if typ in ("damper", "gap", "hook", "damper_bilinear",
                   "plastic_wen", "brb"):
            pi, pj = (x, 0.0, 0.0), (x + 2.0, 0.0, 0.0)
        else:
            pi, pj = (x, 0.0, 0.0), (x, 0.0, 0.5)
        m.add_link(pi, pj, uid=uid, link_type=typ, params=par)
    # cases
    m.mass_source = {"DEAD": 1.0}
    m.add_case("DEAD", {"DEAD": 1.0})
    m.add_case("LIVE", {"LIVE": 1.0})
    m.add_case("TEMP", {"TEMP": 1.0})
    m.add_group("G1", members=[c.uid for (si, _x, _y), c in cols.items()
                               if si == 0] +
                [b.uid for b in m.members if b.uid.startswith(("BX0",
                                                               "BY0"))],
                shells=["F1"])
    m.add_group("G2", members=[mm.uid for mm in m.members
                               if mm.story == "Story2"])
    m.add_staged_case("SC1", "DEAD", include_live={"LIVE": 0.5})
    m.add_staged_case("SC2", "DEAD", stages=[
        {"name": "S1", "duration_days": 14.0, "operations": [
            {"op": "add", "group": "G1", "age_days": 7.0},
            {"op": "load", "group": "G1", "pattern": "DEAD", "scale": 1.0}]},
        {"name": "S2", "operations": [
            {"op": "add", "group": "G2"},
            {"op": "load", "group": "G2", "pattern": "DEAD"}]}])
    m.add_pushover_case("PX", "X", gravity={"DEAD": 1.0, "LIVE": 0.25},
                        target_drift=0.01, steps=20, default_My=150.0,
                        hinges="all_ends", My={"C000": 180.0},
                        hardening=0.03)
    m.add_pushover_case("PY", "Y", target_drift=0.01, steps=15,
                        load_distribution="pattern", pattern="PUSH",
                        control_story="Story2", geometric="pdelta")
    m.add_nonlinear_static_case("NG", [{"pattern": "DEAD", "scale": 1.0}],
                                steps=5)
    m.add_nonlinear_static_case("NX", [{"pattern": "PUSH", "scale": 1.0}],
                                load_application="displacement_control",
                                control_point=[0.0, 0.0, 6.0],
                                control_dof="UX", target_disp=0.02,
                                steps=10, start_from="NG",
                                default_My=150.0)
    add_frequency_function(m, "SSF", "steady_state",
                           [[0.0, 1.0], [5.0, 1.0], [20.0, 0.5]])
    add_frequency_function(m, "PSDF", "psd", [[0.5, 0.01], [10.0, 0.01]])
    add_steady_state_case(m, "SS1", [
        {"pattern": "PUSH", "scale": 2.0, "function": "SSF",
         "phase_deg": 30.0},
        {"pattern": "accel", "direction": "UX", "scale": 0.5,
         "function": "SSF"}],
        freq_start_hz=0.5, freq_end_hz=15.0, n_freq=21,
        output_points=[[0.0, 0.0, 6.0]])
    add_psd_case(m, "PSD1", [{"pattern": "accel", "direction": "UY",
                              "scale": 9.80665, "function": "PSDF"}],
                 freq_start_hz=0.5, freq_end_hz=10.0, n_freq=11,
                 damping_type="hysteretic", damping=0.03)
    m.num_modes = 4
    m.validate()
    return m
