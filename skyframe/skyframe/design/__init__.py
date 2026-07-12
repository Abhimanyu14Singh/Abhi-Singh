"""SkyFrame design-check package (PRELIMINARY).

* :mod:`skyframe.design.steel`    — preliminary AISC 360-16 LRFD screening
  checks for steel W-shape frame members (v0.4).
* :mod:`skyframe.design.concrete` — preliminary ACI 318-19 style checks for
  rectangular concrete beams and columns (v0.6).
* :mod:`skyframe.design.wall`     — preliminary ACI 318 uniform-reinforcing
  shear wall pier checks: strip-integrated PMM, §11.5.4.3 shear, and the
  §18.10.6.3 boundary-element trigger (v0.18).
* :mod:`skyframe.design.punching` — preliminary ACI two-way (punching)
  shear checks at columns supporting meshed shell slabs (v0.18).
* :mod:`skyframe.design.composite` — preliminary AISC 360-16 Chapter I3
  composite beam checks (studs, partial composite, C-I3-4 deflection) for
  W-shape beams supporting meshed shell slabs (v0.20).
* :mod:`skyframe.design.slab`     — preliminary ETABS-style column/middle
  strip flexural design of meshed shell slabs (v0.20).
* :mod:`skyframe.design.vibration` — preliminary AISC Design Guide 11
  walking-vibration screening of slab-supporting beams (v0.20).
* :mod:`skyframe.design.steel_ec3` — preliminary EN 1993-1-1 (EC3)
  member checks: §6.2 cross-section, §6.3.1 buckling curves, §6.3.3
  Annex B interaction (v0.23).
* :mod:`skyframe.design.concrete_ec2` — preliminary EN 1992-1-1 (EC2)
  checks: stress-block flexure, §6.2 shear, strain-compatibility
  columns with gamma_C/gamma_S material factors (v0.23).
* :mod:`skyframe.design.seismic341` — preliminary AISC 341 SMF joint
  screens: strong-column/weak-beam (E3-1) + panel-zone shear (v0.23).

The ``summarize`` roll-ups are exported here under distinct names
(``summarize`` = steel, kept for backward compatibility;
``summarize_concrete`` = concrete; ``summarize_walls`` = wall;
``summarize_composite`` = composite; ``summarize_ec3`` / ``summarize_ec2``
/ ``summarize_341`` = the v0.23 modules).
"""

from skyframe.design.composite import (
    CompositeBeamCheck,
    camber_recommendation,
    check_composite_beams,
    composite_flexure,
    equivalent_inertia,
    stud_strength,
    summarize_composite,
    transformed_inertia,
)
from skyframe.design.concrete_ec2 import (
    beam_flexure_ec2,
    check_concrete_members_ec2,
    check_concrete_members_ec2_envelope,
    column_interaction_ec2,
    shear_vrdc,
    shear_vrds,
    summarize_ec2,
)
from skyframe.design.seismic341 import (
    JointCheck341,
    check_seismic341,
    panel_zone_capacity,
    summarize_341,
)
from skyframe.design.steel_ec3 import (
    MemberCheckEC3,
    buckling_chi,
    buckling_curve,
    check_members_ec3,
    check_members_ec3_envelope,
    summarize_ec3,
)
from skyframe.design.concrete import (
    ConcreteCheck,
    RebarLayout,
    axial_capacity_at_moment,
    check_concrete_members,
    check_concrete_members_envelope,
    column_interaction,
    moment_capacity_at_axial,
)
from skyframe.design.concrete import summarize as summarize_concrete
from skyframe.design.punching import (
    PunchingCheck,
    check_punching,
    default_gravity_case,
    punching_capacity,
)
from skyframe.design.slab import (
    check_slab_strips,
    required_steel,
    strip_layout,
)
from skyframe.design.steel import (
    MemberCheck,
    SectionSuggestion,
    apply_suggestions,
    check_members,
    optimize_members,
    summarize,
)
from skyframe.design.vibration import (
    VibrationCheck,
    check_vibration,
    natural_frequency,
    walking_acceleration,
)
from skyframe.design.wall import (
    WallPierCheck,
    boundary_element_check,
    check_wall_piers,
    fc_from_E,
    summarize_walls,
    wall_interaction,
    wall_shear_strength,
)

__all__ = [
    "MemberCheck", "check_members", "summarize",
    "SectionSuggestion", "optimize_members", "apply_suggestions",
    "ConcreteCheck", "RebarLayout", "check_concrete_members",
    "check_concrete_members_envelope", "column_interaction",
    "axial_capacity_at_moment", "moment_capacity_at_axial",
    "summarize_concrete",
    "WallPierCheck", "check_wall_piers", "wall_interaction",
    "wall_shear_strength", "boundary_element_check", "fc_from_E",
    "summarize_walls",
    "PunchingCheck", "check_punching", "punching_capacity",
    "default_gravity_case",
    "CompositeBeamCheck", "check_composite_beams", "composite_flexure",
    "stud_strength", "transformed_inertia", "equivalent_inertia",
    "summarize_composite",
    "check_slab_strips", "required_steel", "strip_layout",
    "VibrationCheck", "check_vibration", "natural_frequency",
    "walking_acceleration",
    "MemberCheckEC3", "check_members_ec3", "check_members_ec3_envelope",
    "summarize_ec3", "buckling_chi", "buckling_curve",
    "check_concrete_members_ec2", "check_concrete_members_ec2_envelope",
    "summarize_ec2", "beam_flexure_ec2", "shear_vrdc", "shear_vrds",
    "column_interaction_ec2",
    "JointCheck341", "check_seismic341", "summarize_341",
    "panel_zone_capacity", "camber_recommendation",
]
