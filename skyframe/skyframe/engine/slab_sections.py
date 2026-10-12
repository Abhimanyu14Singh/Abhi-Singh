"""OpenSees sections of orthotropic / laminated / ribbed / waffle / deck
shell sections (Wave B; CONTRACT "Orthotropic materials, slab / deck
property types ...").

Every such section is reduced to its resultant stiffness per unit width
(membrane ``A``, bending ``D``, transverse shear ``S13`` / ``S23``, element
local axes; :mod:`skyframe.core.ortho`, :mod:`skyframe.core.slab_props`),
the ETABS modifiers and the shell-type factors are applied exactly as for
an isotropic section (``Q11 *= k11``, ``Q22 *= k22``, ``Q66 *= k12``,
``Q12 *= sqrt(k11 k22)``; S13 / S23 by v13 / v23), ``mod`` and the
cracked-slab ``scale`` multiply everything, and the result is built as the
exactly-equivalent three-layer ``ElasticOrthotropic`` + ``PlateFiber``
``LayeredShell`` of :func:`skyframe.core.modifiers.skin_core_layers`.
Sections that are none of these never reach this module (the legacy
calls stay bit-identical).
"""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import ortho as _ortho
from skyframe.core import shell_types as _st
from skyframe.core import slab_props as _slab
from skyframe.core.modifiers import skin_core_layers


def is_special(ssec, mat) -> bool:
    return (getattr(ssec, "laminate", None) is not None
            or _slab.slab_type(ssec) in _slab.STIFF_TYPES
            or _ortho.is_ortho(mat))


def base_resultants(ssec, mat, materials=None
                    ) -> Tuple[np.ndarray, np.ndarray, float, float]:
    """(A, D, S13, S23) per unit width before modifiers."""
    lam = getattr(ssec, "laminate", None)
    if lam is not None:
        A, _, D, (s13, s23) = _ortho.laminate_resultants(lam, materials)
        return A, D, s13, s23
    r = _slab.equivalent_resultants(ssec, mat)
    if r is not None:
        return r
    return _ortho.homogeneous_resultants(mat, ssec.thickness)


def _factors(ssec):
    extra = _st.type_factors(_st.shell_type_of(ssec))
    return {k: float(getattr(ssec, k, 1.0)) * extra.get(k, 1.0)
            for k in ("f11", "f22", "f12", "m11", "m22", "m12", "v13",
                      "v23")}


def _apply(M: np.ndarray, k11: float, k22: float, k12: float) -> np.ndarray:
    M = np.array(M, dtype=float)
    M[0, 0] *= k11
    M[1, 1] *= k22
    M[0, 1] *= math.sqrt(k11 * k22)
    M[1, 0] *= math.sqrt(k11 * k22)
    M[2, 2] *= k12
    return M


def modified_resultants(ssec, mat, scale: float = 1.0, materials=None):
    A, D, S13, S23 = base_resultants(ssec, mat, materials)
    k = _factors(ssec)
    f = float(ssec.mod) * float(scale)
    A = f * _apply(A, k["f11"], k["f22"], k["f12"])
    D = f * _apply(D, k["m11"], k["m22"], k["m12"])
    return A, D, f * k["v13"] * S13, f * k["v23"] * S23


def build(stag: int, ssec, mat, scale: float, nd_tag: int,
          materials=None) -> Tuple[int, str]:
    A, D, S13, S23 = modified_resultants(ssec, mat, scale, materials)
    tl, mats = skin_core_layers(A, D, S13, S23)
    fibers = []
    for (E1, E2, nu12, G12, G13, G23) in mats:
        nd_tag += 1
        # same slot convention as engine.shell_modifiers (G13 -> "Gyz")
        ops.nDMaterial("ElasticOrthotropic", nd_tag, E1, E2, max(E1, E2),
                       nu12, 0.0, 0.0, G12, G13, G23)
        nd_tag += 1
        ops.nDMaterial("PlateFiber", nd_tag, nd_tag - 1)
        fibers.append(nd_tag)
    skin, core = fibers
    ops.section("LayeredShell", stag, 3, skin, tl, core, tl, skin, tl)
    return nd_tag, "orthotropic"
