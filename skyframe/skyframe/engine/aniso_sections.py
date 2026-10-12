"""OpenSees sections of anisotropic / rotated-orthotropic shells, laminates
at any angle and layered shells with any layer material (Wave C).

CONTRACT "Anisotropic shells, material angle, Section Designer
moment-curvature, layered shells with any material, results CSV folder".
The math (rotation, laminate / layered resultants and the exact 11-slice
stack) lives in :mod:`skyframe.core.aniso`; this module only issues the
OpenSees commands.  Sections for which
:func:`skyframe.core.aniso.needs_general` is False never reach it (every
legacy section keeps its exact previous calls).

* homogeneous anisotropic / rotated orthotropic shells and general
  laminates (LINEAR builds): the modified resultants ``A`` / ``D`` / ``S``
  (ETABS f / m / v factors, ``mod``, shell-type factors and the cracked
  ``scale`` - the same rules as :mod:`skyframe.engine.slab_sections`) are
  realised exactly - with the Wave B skin / core 3-layer stack when there
  is no normal-shear coupling (e.g. a 90-degree material angle), else with
  the 11-slice stack;
* layered sections with an ``elastic`` / ``orthotropic`` / ``timber`` /
  ``user`` layer: the REAL stack (layers bottom -> top) in every build.
  Linear builds use each layer's elastic stiffness; nonlinear builds keep
  the legacy concrete / steel layer laws (via the engine's own code) and
  add the new kinds through :func:`layer_args`.
"""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import aniso as _an
from skyframe.core.modifiers import skin_core_layers

#: uniaxial material tags for PlateRebar fibres (own range, far above the
#: engine's uniaxial counter)
_UNI_TAG0 = 5_000_000


def _ortho_nd(nd_tag: int, spec) -> int:
    """ElasticOrthotropic + PlateFiber for an ("ortho", ...) spec; returns
    the PlateFiber tag (= the advanced nd counter)."""
    _, E1, E2, nu12, G12, G13, G23 = spec
    nd_tag += 1
    # same slot convention as engine.shell_modifiers (G13 -> "Gyz"); the
    # through-thickness terms are decoupled so sigma_zz = 0 condenses out
    ops.nDMaterial("ElasticOrthotropic", nd_tag, E1, E2,
                   max(abs(E1), abs(E2)), nu12, 0.0, 0.0, G12, G13, G23)
    nd_tag += 1
    ops.nDMaterial("PlateFiber", nd_tag, nd_tag - 1)
    return nd_tag


def _fibre_nd(nd_tag: int, E: float, angle: float) -> int:
    nd_tag += 1
    uni = _UNI_TAG0 + nd_tag
    ops.uniaxialMaterial("Elastic", uni, float(E))
    ops.nDMaterial("PlateRebar", nd_tag, uni, float(angle))
    return nd_tag


def slice_args(sl, nd_tag: int) -> Tuple[List[float], int]:
    """LayeredShell (mat, t) argument pairs of a slice stack."""
    args: List[float] = []
    for t, sp in sl:
        if sp[0] == "ortho":
            nd_tag = _ortho_nd(nd_tag, sp)
        else:
            nd_tag = _fibre_nd(nd_tag, sp[1], sp[2])
        args.extend([nd_tag, float(t)])
    return args, nd_tag


def modified_resultants(ssec, mat, scale: float = 1.0, materials=None):
    """(A, D, S13, S23) with mod / f / m / v / shell-type factors / scale."""
    from skyframe.engine.slab_sections import _factors
    A, D, S13, S23 = _an.base_resultants(ssec, mat, materials)
    k = _factors(ssec)
    f = float(ssec.mod) * float(scale)
    A = f * _an.apply_factors(A, k["f11"], k["f22"], k["f12"])
    D = f * _an.apply_factors(D, k["m11"], k["m22"], k["m12"])
    return A, D, f * k["v13"] * S13, f * k["v23"] * S23


def build(stag: int, ssec, mat, scale: float, nd_tag: int,
          materials=None) -> Tuple[int, str]:
    """Linear section ``stag`` of a :func:`needs_general` section."""
    if _an.layered_general(ssec):
        return build_layered_linear(stag, ssec, scale, nd_tag, materials)
    A, D, S13, S23 = modified_resultants(ssec, mat, scale, materials)
    if not (_an.coupled(A) or _an.coupled(D)):
        A0 = np.array(A)
        A0[0, 2] = A0[2, 0] = A0[1, 2] = A0[2, 1] = 0.0
        D0 = np.array(D)
        D0[0, 2] = D0[2, 0] = D0[1, 2] = D0[2, 1] = 0.0
        tl, mats = skin_core_layers(A0, D0, S13, S23)
        fibers = []
        for m6 in mats:
            nd_tag = _ortho_nd(nd_tag, ("ortho",) + tuple(m6))
            fibers.append(nd_tag)
        skin, core = fibers
        ops.section("LayeredShell", stag, 3, skin, tl, core, tl, skin, tl)
        return nd_tag, "orthotropic"
    h = float(getattr(ssec, "thickness", 0.0) or 0.0)
    lam = getattr(ssec, "laminate", None)
    if lam is not None:
        h = sum(float(la["t"]) for la in lam["layers"])
    sl = _an.slices(A, D, S13, S23, h if h > 0 else 0.1)
    args, nd_tag = slice_args(sl, nd_tag)
    ops.section("LayeredShell", stag, len(sl), *args)
    return nd_tag, "anisotropic"


def _layer_slices(la, materials, mod: float):
    """Exact slice stack of ONE layer about its own centre (continuum
    D = t^3/12 Q, shear t G - the LayeredShell rule, no 5/6, as the legacy
    layers); :func:`skyframe.core.aniso.layer_stack`."""
    t = float(la["t"])
    Q, g13, g23 = _an.layer_elastic(la, materials, mod)
    return _an.layer_stack(Q, g13, g23, t,
                           _an.layer_fibre(la, materials, mod))


def build_layered_linear(stag: int, ssec, scale: float, nd_tag: int,
                         materials) -> Tuple[int, str]:
    """Elastic real stack of a layered section with a new layer kind:
    every layer (concrete / steel included) at its initial elastic
    stiffness and its own position, realised exactly per layer."""
    f = float(ssec.mod) * float(scale)
    args: List[float] = []
    n = 0
    for la in ssec.layered["layers"]:
        sl = _layer_slices(la, materials, f)
        a, nd_tag = slice_args(sl, nd_tag)
        args.extend(a)
        n += len(sl)
    ops.section("LayeredShell", stag, n, *args)
    return nd_tag, "layered_general"


def layer_args(la, ssec, materials, mtag: int, nd_tag: int,
               sublayers: int) -> Tuple[List[float], int, int]:
    """NONLINEAR-build LayeredShell args of one NEW-kind layer.

    * elastic / orthotropic / timber: elastic, exact per layer - an
      isotropic or orthotropic material at 0 / 90 degrees is one
      ElasticOrthotropic + PlateFiber split into ``sublayers`` (as the
      legacy layers), anything else the 11-slice stack;
    * user: the material's stress-strain law (E = E * mod) as PlateRebar
      at the layer angle, split into ``sublayers``.
    Returns (args, mtag, nd_tag)."""
    mat = materials[la["material"]]
    mod = float(ssec.mod)
    t = float(la["t"])
    if la["kind"] == "user":
        law = _an.user_law(mat, E=mat.E * mod)
        mtag += 1
        law.ops_material(ops, mtag)
        nd_tag += 1
        ops.nDMaterial("PlateRebar", nd_tag, mtag,
                       float(la.get("angle", 0.0) or 0.0))
        return [nd_tag, t / sublayers] * sublayers, mtag, nd_tag
    Q, g13, g23 = _an.layer_elastic(la, materials, mod)
    if not _an.coupled(Q):
        from skyframe.core.modifiers import orthotropic_from_q
        E1, E2, nu12, G12 = orthotropic_from_q(Q)
        nd_tag = _ortho_nd(nd_tag, ("ortho", E1, E2, nu12, G12, g13, g23))
        return [nd_tag, t / sublayers] * sublayers, mtag, nd_tag
    sl = _an.slices(t * Q, t ** 3 / 12.0 * Q, t * g13, t * g23, t)
    args, nd_tag = slice_args(sl, nd_tag)
    return args, mtag, nd_tag
