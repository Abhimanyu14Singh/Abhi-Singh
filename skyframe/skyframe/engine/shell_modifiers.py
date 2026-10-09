"""OpenSees section wiring for the ETABS shell modifiers (f/m/v factors).

``elastic_shell_section`` replaces the engine's single
``ElasticMembranePlateSection`` call for LINEAR shell sections:

* all eight stiffness factors 1.0 -> the EXACT legacy call
  ``ElasticMembranePlateSection(E*mod*scale, nu, t, 0.0)`` (bit-identical);
* ``f11 == f22 == f12 = f`` and ``m11 == m22 == m12 == v13 == v23 = b`` ->
  ``ElasticMembranePlateSection(E' * f, nu, t, 0.0, b/f)`` — the optional
  ``Ep_mod`` scales plate bending and transverse shear (probed), so the
  result is exact;
* anything else (directional 11/22/12 factors, shear factors different
  from the bending factor) -> an exactly-equivalent 3-layer orthotropic
  ``LayeredShell`` (``ElasticOrthotropic`` + ``PlateFiber``), see
  :func:`skyframe.core.modifiers.skin_core_layers`.
"""

from __future__ import annotations

from typing import Tuple

import openseespy.opensees as ops

from skyframe.core.modifiers import (shell_mods_default,
                                     shell_resultant_stiffness,
                                     shell_uniform_factors, skin_core_layers)


def elastic_shell_section(stag: int, ssec, mat, scale: float,
                          nd_tag: int) -> Tuple[int, str]:
    """Create the linear shell section ``stag``.

    ``scale`` is the cracked-slab factor on E (1.0 = none; multiplied in
    only when != 1.0 so the legacy argument is unchanged).  Returns the
    advanced nDMaterial tag counter and the path taken
    (``"legacy" | "uniform" | "layered"``).
    """
    E = mat.E * ssec.mod
    if scale != 1.0:
        E = E * scale
    t = ssec.total_thickness
    if shell_mods_default(ssec):
        ops.section("ElasticMembranePlateSection", stag, E, mat.nu, t, 0.0)
        return nd_tag, "legacy"
    uni = shell_uniform_factors(ssec)
    if uni is not None:
        f, b = uni
        args = [E * f, mat.nu, t, 0.0]
        if b != f:
            args.append(b / f)
        ops.section("ElasticMembranePlateSection", stag, *args)
        return nd_tag, "uniform"
    A, B, S13, S23 = shell_resultant_stiffness(E, mat.nu, t, ssec)
    tl, mats = skin_core_layers(A, B, S13, S23)
    fibers = []
    for (E1, E2, nu12, G12, G13, G23) in mats:
        nd_tag += 1
        # ElasticOrthotropic: Ex Ey Ez nu_xy nu_yz nu_zx Gxy Gyz Gzx; the
        # through-thickness terms are decoupled (nu_yz = nu_zx = 0) so the
        # plate-fiber condensation (sigma_zz = 0) returns exactly Q.
        # LayeredShellFiberSection feeds the section's gamma_13 (Vxz) into
        # the plate fiber's 4th shear slot, which ElasticOrthotropic labels
        # Gyz (probed: a soft "Gzx" changes only the x-z response) — hence
        # G13 goes in the Gyz position and G23 in the Gzx position.
        ops.nDMaterial("ElasticOrthotropic", nd_tag, E1, E2, max(E1, E2),
                       nu12, 0.0, 0.0, G12, G13, G23)
        nd_tag += 1
        ops.nDMaterial("PlateFiber", nd_tag, nd_tag - 1)
        fibers.append(nd_tag)
    skin, core = fibers
    ops.section("LayeredShell", stag, 3, skin, tl, core, tl, skin, tl)
    return nd_tag, "layered"
