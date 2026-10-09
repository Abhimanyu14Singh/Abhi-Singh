"""Hinge duplicate-node ties at partially restrained originals.

A plastic hinge (pushover / nonlinear static / nonlinear time history)
connects the member end to a DUPLICATE node; the duplicate's translations
were tied to the original with ``equalDOF`` under the Transformation
constraint handler.  When the original (retained) node carries a PARTIAL
single-point restraint on the tied dofs -- the typical case is a column
base on a grounded spring support (ux sprung and therefore free, uy/uz
fixed), but also a roller support or a dof dropped by Set Active DOFs --
openseespy 3.7.1's Transformation handler mis-condenses the tie: the
duplicate's FREE translation is held at zero.  The member then sees a
fixed base, so the support spring (its zeroLength between the co-located
ground node and the original) is bypassed: a cantilever on a translational
base spring with a base hinge showed the fixed-base stiffness instead of
spring + hinge + column in series.

Remedy (decided here, applied in ``OpenSeesEngine._build``): such ties use
the existing v0.19 stiff zeroLength tie ELEMENT (k_tie = 1e8 x the member
end stiffness; relative softening ~1e-8) instead of the MP constraint.
Fully restrained and fully free originals keep the exact equalDOF path, so
every model without a partially restrained hinge node is bit-identical.
"""

from typing import Callable, Sequence


def partial_sp_checker(asm, active_mask: Sequence[int]
                       ) -> Callable[[int, Sequence[int]], bool]:
    """Return ``f(orig_tag, dofs)``: True when the original node is
    restrained on SOME but not ALL of the 1-based tied ``dofs``.

    Restraints are the build-time record (``asm.node_restraints``: supports
    with sprung dofs cleared, auto-restraints) plus every dof outside the
    model's active-dof mask (``_restrain_inactive_dofs`` fixes those after
    the ties exist, which would recreate the same partial pattern).
    """
    def check(orig: int, dofs: Sequence[int]) -> bool:
        r = asm.node_restraints.get(orig, (0,) * 6)
        fixed = [bool(r[d - 1]) or not active_mask[d - 1] for d in dofs]
        return any(fixed) and not all(fixed)
    return check
