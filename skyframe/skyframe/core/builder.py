"""Parametric building generator — the "quick model" ETABS-style wizard."""

from __future__ import annotations

from typing import List, Optional

from .model import BuildingModel, FrameSection, GridSystem, Material, MemberUDL, StoryForce


def quick_building(
    name: str = "Quick Building",
    bays_x: int = 3,
    bay_width_x: float = 6.0,
    bays_y: int = 2,
    bay_width_y: float = 6.0,
    stories: int = 4,
    story_height: float = 3.2,
    first_story_height: Optional[float] = None,
    E: float = 25_000_000.0,          # kPa (~25 GPa concrete)
    column_size: float = 0.5,          # m square
    beam_b: float = 0.3,
    beam_h: float = 0.6,
    dead_udl: float = 25.0,            # kN/m on every beam
    live_udl: float = 10.0,            # kN/m on every beam
    quake_coeff: float = 0.08,         # base shear V = C * seismic weight
    base_fixity: str = "fixed",
) -> BuildingModel:
    """Generate a regular moment-frame building on an orthogonal grid.

    Lateral load pattern: equivalent static, inverted-triangular distribution
    F_i = V * (w_i h_i) / sum(w_j h_j)  (ASCE 7 k = 1).
    """
    mdl = BuildingModel(name=name)
    mdl.add_material(Material("CONC", E=E, nu=0.2, unit_weight=24.0))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", column_size, column_size))
    mdl.add_section(FrameSection.rectangular("BEAM", "CONC", beam_b, beam_h))

    xs = [i * bay_width_x for i in range(bays_x + 1)]
    ys = [j * bay_width_y for j in range(bays_y + 1)]
    mdl.grid = GridSystem(xs, ys)

    heights: List[float] = [(first_story_height or story_height)] + \
                           [story_height] * (stories - 1)
    mdl.set_stories(heights)
    mdl.base_fixity = base_fixity

    dead = mdl.pattern("DEAD", "dead")
    live = mdl.pattern("LIVE", "live")

    # columns + beams story by story
    for si, st in enumerate(mdl.stories):
        z_top = st.elevation
        z_bot = st.elevation - st.height
        for yi, y in enumerate(ys):
            for xi, x in enumerate(xs):
                mdl.add_member(
                    "column", "COL", (x, y, z_bot), (x, y, z_top), story=st.name,
                    uid=f"C{si + 1}-{mdl.grid.x_labels[xi]}{mdl.grid.y_labels[yi]}")
        for yi, y in enumerate(ys):
            for xi in range(len(xs) - 1):
                m = mdl.add_member(
                    "beam", "BEAM", (xs[xi], y, z_top), (xs[xi + 1], y, z_top),
                    story=st.name,
                    uid=f"BX{si + 1}-{mdl.grid.x_labels[xi]}{mdl.grid.y_labels[yi]}")
                dead.member_udls.append(MemberUDL(m.uid, dead_udl))
                live.member_udls.append(MemberUDL(m.uid, live_udl))
        for xi, x in enumerate(xs):
            for yi in range(len(ys) - 1):
                m = mdl.add_member(
                    "beam", "BEAM", (x, ys[yi], z_top), (x, ys[yi + 1], z_top),
                    story=st.name,
                    uid=f"BY{si + 1}-{mdl.grid.x_labels[xi]}{mdl.grid.y_labels[yi]}")
                dead.member_udls.append(MemberUDL(m.uid, dead_udl))
                live.member_udls.append(MemberUDL(m.uid, live_udl))

    mdl.mass_from_patterns = {"DEAD": 1.0}

    # equivalent static earthquake: V = C * W, triangular over height
    masses = mdl.compute_story_masses()
    W = sum(masses.values()) * 9.80665
    V = quake_coeff * W
    wh = {s.name: masses[s.name] * 9.80665 * s.elevation for s in mdl.stories}
    sum_wh = sum(wh.values()) or 1.0
    eqx = mdl.pattern("EQX", "quake")
    eqy = mdl.pattern("EQY", "quake")
    for s in mdl.stories:
        f = V * wh[s.name] / sum_wh
        eqx.story_forces.append(StoryForce(s.name, fx=f))
        eqy.story_forces.append(StoryForce(s.name, fy=f))

    mdl.add_case("DEAD", {"DEAD": 1.0})
    mdl.add_case("LIVE", {"LIVE": 1.0})
    mdl.add_case("EQX", {"EQX": 1.0})
    mdl.add_case("EQY", {"EQY": 1.0})
    mdl.add_combo("1.2D + 1.6L", {"DEAD": 1.2, "LIVE": 1.6})
    mdl.add_combo("1.2D + 1.0L + 1.0EX", {"DEAD": 1.2, "LIVE": 1.0, "EQX": 1.0})
    mdl.add_combo("1.2D + 1.0L + 1.0EY", {"DEAD": 1.2, "LIVE": 1.0, "EQY": 1.0})
    mdl.add_combo("0.9D + 1.0EX", {"DEAD": 0.9, "EQX": 1.0})
    mdl.num_modes = min(3 * stories, 12)
    return mdl
