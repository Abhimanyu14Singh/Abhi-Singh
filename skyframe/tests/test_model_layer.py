"""Model-layer tests (no engine, no solver): geometry, mass, sections, dicts.

All expected values are computed in the tests from first principles:
  * member counts of a regular grid frame,
  * story elevations = cumulative heights,
  * story mass = sum(w * L) / g over the beams of the story,
  * rectangular section properties A = b*h, I = b*h^3/12,
    J (Roark) ~= 0.1406 * s^4 for a square,
  * equivalent-static EQ force distribution F_i = V * w_i h_i / sum(w_j h_j).
"""

import json
import math

import pytest

from skyframe.core.builder import quick_building
from skyframe.core.model import (
    G_ACCEL,
    BuildingModel,
    FrameSection,
    Material,
)


# --------------------------------------------------------------------------- #
# quick_building geometry
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bays_x, bays_y, stories",
    [(3, 2, 4),   # the quick_building defaults
     (1, 1, 3),
     (2, 4, 1)],
)
def test_quick_building_member_counts(bays_x, bays_y, stories):
    """Members per story: (bx+1)(by+1) columns + bx(by+1) X-beams + by(bx+1) Y-beams."""
    mdl = quick_building(bays_x=bays_x, bays_y=bays_y, stories=stories)

    cols_per_story = (bays_x + 1) * (bays_y + 1)
    xbeams_per_story = bays_x * (bays_y + 1)
    ybeams_per_story = bays_y * (bays_x + 1)

    cols = [m for m in mdl.members if m.kind == "column"]
    beams = [m for m in mdl.members if m.kind == "beam"]
    assert len(cols) == stories * cols_per_story
    assert len(beams) == stories * (xbeams_per_story + ybeams_per_story)
    assert len(mdl.members) == stories * (cols_per_story + xbeams_per_story
                                          + ybeams_per_story)

    # every story owns the same number of members
    for st in mdl.stories:
        n = sum(1 for m in mdl.members if m.story == st.name)
        assert n == cols_per_story + xbeams_per_story + ybeams_per_story


def test_quick_building_story_elevations():
    """Elevations are cumulative story heights (first story may differ)."""
    mdl = quick_building(stories=4, story_height=3.2, first_story_height=4.5)
    heights = [4.5, 3.2, 3.2, 3.2]
    elev = 0.0
    expected = {}
    for i, h in enumerate(heights):
        elev += h
        expected[f"Story{i + 1}"] = elev
    assert mdl.story_elevations() == pytest.approx(expected)
    assert [s.name for s in mdl.stories] == list(expected)  # bottom -> top


def test_compute_story_masses_matches_hand_calc():
    """Story mass = sum(w * L) over the story's beams / g (mass from DEAD)."""
    bays_x, bays_y = 3, 2
    bwx = bwy = 6.0
    dead_udl = 25.0
    mdl = quick_building(bays_x=bays_x, bay_width_x=bwx,
                         bays_y=bays_y, bay_width_y=bwy,
                         stories=4, dead_udl=dead_udl)

    beam_len_per_story = (bays_x * (bays_y + 1) * bwx
                          + bays_y * (bays_x + 1) * bwy)   # 102 m for defaults
    expected_mass = dead_udl * beam_len_per_story / G_ACCEL  # tonne

    masses = mdl.compute_story_masses()
    assert set(masses) == {s.name for s in mdl.stories}
    for name, m in masses.items():
        assert m == pytest.approx(expected_mass, rel=1e-12), name


def test_compute_story_masses_explicit_override():
    """Explicit story_masses win over pattern-derived mass."""
    mdl = quick_building(stories=2)
    mdl.story_masses["Story1"] = 123.0
    masses = mdl.compute_story_masses()
    assert masses["Story1"] == 123.0
    assert masses["Story2"] > 0.0  # still pattern-derived


def test_quick_building_eq_pattern_hand_calc():
    """EQX: total = C*W, distributed F_i = V * m_i h_i / sum(m_j h_j)."""
    coeff = 0.08
    mdl = quick_building(quake_coeff=coeff)
    masses = mdl.compute_story_masses()
    W = sum(masses.values()) * G_ACCEL
    V = coeff * W
    wh = {s.name: masses[s.name] * G_ACCEL * s.elevation for s in mdl.stories}
    sum_wh = sum(wh.values())

    forces = {sf.story: sf.fx for sf in mdl.patterns["EQX"].story_forces}
    assert sum(forces.values()) == pytest.approx(V, rel=1e-12)
    for s in mdl.stories:
        assert forces[s.name] == pytest.approx(V * wh[s.name] / sum_wh, rel=1e-12)


# --------------------------------------------------------------------------- #
# Section property math
# --------------------------------------------------------------------------- #
def test_rectangular_section_properties():
    """A = b*h, I33 = b*h^3/12 (strong), I22 = h*b^3/12 (weak)."""
    b, h = 0.3, 0.6
    sec = FrameSection.rectangular("B30x60", "CONC", b, h)
    assert sec.A == pytest.approx(b * h, rel=1e-12)                  # 0.18
    assert sec.I33 == pytest.approx(b * h ** 3 / 12.0, rel=1e-12)    # 0.0054
    assert sec.I22 == pytest.approx(h * b ** 3 / 12.0, rel=1e-12)    # 0.00135
    assert sec.I33 > sec.I22
    # J must be positive and strictly below the polar moment I22 + I33
    assert 0.0 < sec.J < sec.I22 + sec.I33


def test_square_section_torsion_constant():
    """Square torsion constant: J = beta * s^4 with beta = 0.1406 (Roark/
    St-Venant tabulated value for a/b = 1)."""
    s = 0.5
    sec = FrameSection.rectangular("SQ", "CONC", s, s)
    assert sec.I33 == pytest.approx(sec.I22, rel=1e-12)
    assert sec.J == pytest.approx(0.1406 * s ** 4, rel=2e-3)


def test_material_shear_modulus():
    """G = E / (2 (1 + nu))."""
    mat = Material("CONC", E=25_000_000.0, nu=0.2)
    assert mat.G == pytest.approx(25_000_000.0 / 2.4, rel=1e-12)


# --------------------------------------------------------------------------- #
# Validation errors
# --------------------------------------------------------------------------- #
def test_validation_unknown_names_raise():
    mdl = BuildingModel(name="v")
    mdl.add_material(Material("CONC", E=25e6))
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.4, 0.4))

    with pytest.raises(ValueError):
        mdl.add_section(FrameSection.rectangular("BAD", "NOPE", 0.3, 0.3))
    with pytest.raises(ValueError):
        mdl.add_member("column", "NOSEC", (0, 0, 0), (0, 0, 3))
    with pytest.raises(ValueError):     # zero-length member
        mdl.add_member("column", "COL", (1, 1, 1), (1, 1, 1))

    mdl.pattern("DEAD", "dead")
    with pytest.raises(ValueError):     # unknown pattern in case
        mdl.add_case("C1", {"NOPAT": 1.0})
    mdl.add_case("DEAD", {"DEAD": 1.0})
    with pytest.raises(ValueError):     # unknown case in combo
        mdl.add_combo("CB1", {"NOCASE": 1.4})
    # valid ones do not raise
    mdl.add_combo("CB2", {"DEAD": 1.4})

    with pytest.raises(KeyError):
        mdl.story_by_name("NoStory")


# --------------------------------------------------------------------------- #
# to_dict round trip
# --------------------------------------------------------------------------- #
def test_model_to_dict_shape_and_json_safe():
    """model.to_dict() carries the keys the CONTRACT relies on and is
    JSON-serialisable; members carry pi/pj coordinate triples."""
    mdl = quick_building(bays_x=1, bays_y=1, stories=2)
    d = mdl.to_dict()

    for key in ("name", "materials", "sections", "grid", "stories", "members",
                "base_fixity", "supports", "nodal_masses", "rigid_diaphragms",
                "story_masses", "patterns", "cases", "combos", "num_modes"):
        assert key in d, key

    for m in d["members"]:
        for key in ("uid", "kind", "section", "pi", "pj", "story", "length"):
            assert key in m, key
        assert len(m["pi"]) == 3 and len(m["pj"]) == 3
        assert m["length"] == pytest.approx(math.dist(m["pi"], m["pj"]))

    for s in d["stories"]:
        assert set(s) == {"name", "height", "elevation"}

    assert d["story_masses"] == pytest.approx(mdl.compute_story_masses())
    assert set(d["cases"]) == set(mdl.cases)
    assert set(d["combos"]) == set(mdl.combos)

    json.dumps(d)   # must be JSON-safe (raises on failure)
