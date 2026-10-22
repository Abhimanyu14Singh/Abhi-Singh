"""FNA with a device link to an explicitly SUPPORTED ground joint.

The FNA linearized model drops device links (damper / gap / hook), so a
joint only such a link touched has no FE node there; a support placed on it
(the way ETABS models a damper to ground) used to stop the eigen build with
"No FE node at point".  The linearized build now skips exactly those
supports (the FNA deformation rows already treat a missing anchor as
ground), so the run equals the unsupported-anchor run and direct
integration; a support on a point that is no device anchor still raises.
"""

import math

import numpy as np
import pytest

from skyframe.core.model import (BuildingModel, FrameSection, Material,
                                 NodalMass, PointSupport)
from skyframe.engine.opensees_engine import OpenSeesEngine

ops = pytest.importorskip("openseespy.opensees")

ANCHOR = (1.5, 0.0, 3.0)


def _model(support_anchor: bool, extra_support=None):
    mdl = BuildingModel(name="anchor")
    mdl.rigid_diaphragms = False
    mdl.num_modes = 1
    mdl.add_material(Material("S", E=2.0e8, nu=0.3))
    mdl.add_section(FrameSection.rectangular("C", "S", 0.3, 0.3))
    mdl.set_stories([3.0])
    mdl.add_member("column", "C", (0, 0, 0), (0, 0, 3.0), story="Story1",
                   uid="C1")
    mdl.supports.append(PointSupport((0.0, 0.0, 0.0), (1, 1, 1, 1, 1, 1)))
    if support_anchor:
        mdl.supports.append(PointSupport(ANCHOR, (1, 1, 1, 1, 1, 1)))
    if extra_support is not None:
        mdl.supports.append(PointSupport(extra_support, (1, 1, 1, 1, 1, 1)))
    mdl.nodal_masses.append(NodalMass((0, 0, 3.0), mx=10.0))
    dt = 0.002
    acc = [4.0 * math.sin(math.pi * k / 30) for k in range(31)] + [0.0] * 400
    mdl.add_th_case("A", "X", acc, dt, damping=0.02)
    mdl.add_link((0, 0, 3.0), ANCHOR, link_type="damper", uid="D1",
                 params={"cd": 30.0, "alpha": 1.0})
    return mdl


def test_fna_damper_to_supported_ground_joint():
    free = np.array(OpenSeesEngine(_model(False)).run_fna("A")
                    .story_ux["Story1"])
    eng = OpenSeesEngine(_model(True))
    fixed = np.array(eng.run_fna("A").story_ux["Story1"])
    direct = np.array(OpenSeesEngine(_model(True)).run_time_history("A")
                      .story_ux["Story1"])
    pk = np.max(np.abs(fixed))
    assert pk > 0.0
    assert fixed == pytest.approx(free, rel=1e-12, abs=1e-15)
    assert np.max(np.abs(fixed - direct)) <= 0.01 * pk


def test_support_off_any_node_still_raises():
    mdl = _model(True, extra_support=(5.0, 5.0, 3.0))
    with pytest.raises(ValueError, match="No FE node"):
        OpenSeesEngine(mdl).run_fna("A")
