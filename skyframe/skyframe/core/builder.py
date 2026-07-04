"""Parametric building generator — the "quick model" ETABS-style wizard.

v0.4 also hosts the automatic ASCE 7-style wind pattern generator
(:func:`make_wind_pattern` with helpers :func:`wind_kz` / :func:`wind_qz`).
"""

from __future__ import annotations

from typing import List, Optional

from .model import (BuildingModel, FrameSection, GridSystem, LoadPattern,
                    Material, MemberUDL, StoryForce)


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

    mdl.mass_source = {"DEAD": 1.0}
    mdl.mass_from_patterns = {"DEAD": 1.0}   # legacy alias (pre-v0.4 readers)

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


# --------------------------------------------------------------------------- #
# v0.4: automatic ASCE 7-style wind load pattern
# --------------------------------------------------------------------------- #
# ASCE 7-16 Table 26.10-1 power-law parameters: exposure -> (alpha, zg [m])
WIND_EXPOSURES = {
    "B": (7.0, 365.76),
    "C": (9.5, 274.32),
    "D": (11.5, 213.36),
}
WIND_KD = 0.85       # directionality factor Kd (buildings, MWFRS)
WIND_KZT = 1.0       # topographic factor (flat terrain, v0.4)
WIND_Z_MIN = 4.6     # m — 15 ft Kz evaluation floor (applied per contract)


def wind_kz(z: float, exposure: str) -> float:
    """Velocity pressure exposure coefficient Kz (ASCE 7-16 eq. 26.10-1).

    ``Kz = 2.01 (z / zg)^(2/alpha)`` with alpha/zg from Table 26.10-1 and z
    floored at 4.6 m (15 ft).  v0.4 applies the 4.6 m floor to every
    exposure category (documented simplification; ASCE uses 9.14 m for B).
    """
    if exposure not in WIND_EXPOSURES:
        raise ValueError(f"exposure must be one of "
                         f"{sorted(WIND_EXPOSURES)}, got {exposure!r}")
    alpha, zg = WIND_EXPOSURES[exposure]
    zz = max(float(z), WIND_Z_MIN)
    return 2.01 * (zz / zg) ** (2.0 / alpha)


def wind_qz(z: float, basic_wind_speed: float, exposure: str,
            importance: float = 1.0) -> float:
    """Velocity pressure qz in kPa (ASCE 7 style).

    ``qz = 0.613 Kz Kzt Kd V^2 I`` in Pa (V in m/s), converted to kPa.
    Kzt = 1.0 and Kd = 0.85 are fixed in v0.4.
    """
    return (0.613 * wind_kz(z, exposure) * WIND_KZT * WIND_KD
            * float(basic_wind_speed) ** 2 * float(importance)) / 1000.0


def make_wind_pattern(model: BuildingModel, name: str, direction: str,
                      basic_wind_speed: float, exposure: str = "C",
                      cp_total: float = 1.3,
                      importance: float = 1.0) -> LoadPattern:
    """Create (or replace) an auto wind LoadPattern with story forces.

    Story force at each story level (the story's TOP elevation z):
    ``F = qz(z) * cp_total * A_trib`` with the tributary facade area
    ``A_trib = trib_height * width``:

    * trib_height = half the story below the level + half the story above
      (top level: half the top story only);
    * width = plan extent PERPENDICULAR to the wind (direction "X" loads
      the Y-facing facade width, and vice versa), from
      ``model.plan_extents()``.

    ``cp_total`` is the combined windward+leeward pressure coefficient
    (default 1.3, e.g. Cp 0.8 windward + 0.5 leeward, gust factor folded
    in).  The pattern is stored under ``name`` with kind "wind"
    (an existing pattern of the same name is replaced) and returned.
    """
    if direction not in ("X", "Y"):
        raise ValueError(f"direction must be X|Y, got {direction!r}")
    if exposure not in WIND_EXPOSURES:
        raise ValueError(f"exposure must be one of "
                         f"{sorted(WIND_EXPOSURES)}, got {exposure!r}")
    V = float(basic_wind_speed)
    if not V > 0.0:
        raise ValueError("basic_wind_speed must be > 0 (m/s)")
    cp = float(cp_total)
    imp = float(importance)
    if not imp > 0.0:
        raise ValueError("importance must be > 0")
    if not model.stories:
        raise ValueError("model has no stories to load")
    lx, ly = model.plan_extents()
    width = ly if direction == "X" else lx
    if width <= 0.0:
        raise ValueError("plan width perpendicular to the wind is zero")

    pat = LoadPattern(name, "wind")
    heights = [s.height for s in model.stories]
    for i, story in enumerate(model.stories):
        trib_h = heights[i] / 2.0 + (heights[i + 1] / 2.0
                                     if i + 1 < len(heights) else 0.0)
        q = wind_qz(story.elevation, V, exposure, imp)   # kPa at story top
        force = q * cp * trib_h * width                  # kN
        pat.story_forces.append(StoryForce(
            story.name,
            fx=force if direction == "X" else 0.0,
            fy=force if direction == "Y" else 0.0))
    model.patterns[name] = pat
    return pat
