"""Modal analysis with a shell wall in the model (v0.2).

Physics check: adding an in-plane wall to a portal frame adds stiffness but
NO mass (v0.2 ignores shell self-weight per CONTRACT.md, and the model's
masses are explicit NodalMass entries), so every x-sway period must strictly
drop -- and for a 3x3 concrete wall vs a bare portal, drop a lot.
"""

import numpy as np
import pytest

engine_mod = pytest.importorskip("skyframe.engine.opensees_engine")
mesh_mod = pytest.importorskip("skyframe.core.mesh")

from _v02_utils import (          # noqa: E402
    E_CONC, model_mod, FrameSection, NodalLoad, NodalMass, PointSupport,
    has_shell_api, new_model, run,
)

pytestmark = pytest.mark.skipif(
    not has_shell_api(),
    reason="v0.2 shell API (ShellSection/ShellRegion) not present yet")


def _portal(name, with_wall):
    """One-story portal (columns at x=0,3; beam on top), fixed bases, 20 t of
    x-direction mass at each top corner.  Mass is put on ux only so the
    eigenproblem contains exactly the two in-plane sway-ish modes and the
    wall comparison is apples-to-apples (a wall in the x-z plane does not
    stiffen out-of-plane sway, which would otherwise become mode 1 in both
    models and hide the effect)."""
    H = span = 3.0
    mdl = new_model(name, [H])
    mdl.add_section(FrameSection.rectangular("COL", "CONC", 0.35, 0.35))
    mdl.add_section(FrameSection.rectangular("BM", "CONC", 0.3, 0.5))
    mdl.add_member("column", "COL", (0, 0, 0), (0, 0, H), story="Story1", uid="CL")
    mdl.add_member("column", "COL", (span, 0, 0), (span, 0, H), story="Story1", uid="CR")
    mdl.add_member("beam", "BM", (0, 0, H), (span, 0, H), story="Story1", uid="BM")
    mdl.supports.append(PointSupport((0, 0, 0), (1, 1, 1, 1, 1, 1)))
    mdl.supports.append(PointSupport((span, 0, 0), (1, 1, 1, 1, 1, 1)))
    if with_wall:
        mdl.shell_sections["SH"] = model_mod.ShellSection(
            name="SH", material="CONC", thickness=0.15)
        mdl.shells.append(model_mod.ShellRegion(
            uid="W1", kind="wall", behavior="shell", section="SH",
            corners=[(0, 0, 0), (span, 0, 0), (span, 0, H), (0, 0, H)],
            mesh_size=0.5, story="Story1"))
    for x in (0.0, span):
        mdl.nodal_masses.append(NodalMass((x, 0, H), mx=20.0))
    # one token static case so engine.run() has something static to solve
    mdl.pattern("PX", "other").nodal_loads.append(NodalLoad((0, 0, H), fx=1.0))
    mdl.add_case("PX", {"PX": 1.0})
    mdl.num_modes = 2
    return mdl


def _check_modal_block(modal):
    periods = modal["periods"]
    assert len(periods) >= 1
    assert all(T > 0.0 and np.isfinite(T) for T in periods), periods
    # sorted fundamental-first (non-increasing)
    for a, b in zip(periods, periods[1:]):
        assert a >= b - 1e-12, f"periods not sorted: {periods}"
    freqs = modal["frequencies"]
    for T, f in zip(periods, freqs):
        assert f == pytest.approx(1.0 / T, rel=1e-9)
    part = modal["participation"]
    for key in ("ux", "uy", "rz"):
        vals = [p[key] for p in part]
        assert all(v >= -1e-9 for v in vals), f"{key} ratios negative: {vals}"
        # mass-participation ratios of distinct modes can never sum past 1
        assert sum(vals) <= 1.0 + 1e-9, f"sum {key} = {sum(vals)} > 1"
    return periods, part


def test_wall_stiffens_building_modal():
    d_bare = run(_portal("bare", with_wall=False))
    d_wall = run(_portal("walled", with_wall=True))

    p_bare, part_bare = _check_modal_block(d_bare["modal"])
    p_wall, part_wall = _check_modal_block(d_wall["modal"])

    # both eigenproblems have exactly 2 massed DOFs (ux at the two corners),
    # so 2 modes must capture (essentially) all of the x mass
    assert sum(p["ux"] for p in part_bare) == pytest.approx(1.0, abs=1e-6)
    assert sum(p["ux"] for p in part_wall) == pytest.approx(1.0, abs=1e-6)

    # the wall adds stiffness but zero mass -> T1 must strictly drop, and
    # for a full-bay 0.15 m concrete wall vs a bare portal, drop hard.
    # (Rayleigh bound: adding a positive-semidefinite stiffness cannot
    # lengthen any period, so ANY increase is a hard bug.)
    assert p_wall[0] < p_bare[0], (p_wall, p_bare)
    assert p_wall[0] < 0.5 * p_bare[0], (
        f"wall barely stiffened the frame: T1 {p_bare[0]:.4f}s -> "
        f"{p_wall[0]:.4f}s; expected several-fold drop")
