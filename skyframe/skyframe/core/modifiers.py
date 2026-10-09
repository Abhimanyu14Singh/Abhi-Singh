"""Frame shear deformation + full ETABS property modifiers; shell
membrane/bending/shear modifiers (analysis-only).

This module holds the solver-agnostic math; the OpenSees wiring lives in
:mod:`skyframe.engine.opensees_engine` (frame members) and
:mod:`skyframe.engine.shell_modifiers` (shell sections).  See CONTRACT
"Frame shear deformation and full property modifiers; shell
membrane/bending modifiers".

Frame members
-------------
* ``FrameSection.As2`` / ``As3`` (m^2) are the shear areas for shear along
  local 2 (y, major-axis bending about 3) and local 3 (z, minor-axis
  bending about 2).  Shear deformation is ON when ``shear_deformation`` is
  True or either area is set; otherwise the member stays the
  Euler-Bernoulli ``elasticBeamColumn`` (bit-identical legacy path).
* Unset areas of an enabled section are filled from the section shape:
  AISC W-shape (library name) -> ``As2 = d*tw`` (web), ``As3 = 5/3*bf*tf``
  (both flanges, 5/6 * 2*bf*tf); rectangle (``b, h > 0``) -> ``5/6*A`` for
  both.  A direction that cannot be resolved stays shear-RIGID.
* ``mod_As2``/``mod_As3`` scale the shear areas; ``mod_weight`` scales the
  member's self-weight load (and the weight-derived story mass);
  ``mod_mass`` scales the member's element self-mass
  (``mass_source_mode == "element_self_mass"``).

Shells
------
ETABS-style factors on the elastic resultant stiffness of the
ElasticMembranePlateSection that ``ShellSection`` maps to (after ``mod``):

* membrane ``A = h * Q~(f11, f22, f12)``, bending ``B = h^3/12 * Q~(m11,
  m22, m12)``, transverse shear ``S13 = v13 * 5/6*G*h``,
  ``S23 = v23 * 5/6*G*h`` where ``Q~`` is the plane-stress isotropic matrix
  with ``Q11 *= k11``, ``Q22 *= k22``, ``Q66 *= k12`` and the Poisson
  coupling ``Q12 *= sqrt(k11*k22)`` (keeps the matrix positive definite and
  reduces to a plain factor when k11 == k22).
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

# AISC Steel Construction Manual (15th ed.) Table 1-1 web / flange
# thicknesses (in) for the built-in W-shape library
# (:mod:`skyframe.core.sections_library`; d and bf come from there).
W_SHAPE_TW_TF_IN = {
    "W8x31": (0.285, 0.435), "W8x40": (0.360, 0.560),
    "W10x33": (0.290, 0.435), "W10x49": (0.340, 0.560),
    "W12x26": (0.230, 0.380), "W12x40": (0.295, 0.515),
    "W12x65": (0.390, 0.605), "W14x30": (0.270, 0.385),
    "W14x48": (0.340, 0.595), "W14x90": (0.440, 0.710),
    "W16x36": (0.295, 0.430), "W16x57": (0.430, 0.715),
    "W18x50": (0.355, 0.570), "W18x76": (0.425, 0.680),
    "W21x62": (0.400, 0.615), "W21x93": (0.580, 0.930),
    "W24x76": (0.440, 0.680), "W24x104": (0.500, 0.750),
    "W27x94": (0.490, 0.745), "W30x116": (0.565, 0.850),
    "W33x130": (0.580, 0.855), "W36x150": (0.625, 0.940),
}
_IN = 0.0254

FRAME_MOD_KEYS = ("mod_As2", "mod_As3", "mod_mass", "mod_weight")
SHELL_STIFF_KEYS = ("f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23")
SHELL_MOD_KEYS = SHELL_STIFF_KEYS + ("mass", "weight")


# --------------------------------------------------------------------------- #
# frame sections
# --------------------------------------------------------------------------- #
def shear_enabled(sec) -> bool:
    """True when the section asks for Timoshenko shear deformation."""
    return bool(getattr(sec, "shear_deformation", False)
                or getattr(sec, "As2", None) is not None
                or getattr(sec, "As3", None) is not None)


def auto_shear_areas(sec) -> Tuple[Optional[float], Optional[float]]:
    """Shape-derived (As2, As3) in m^2, or None per unresolvable direction.

    AISC library W-shape (matched by section name) -> ``(d*tw, 5/3*bf*tf)``;
    rectangle with drawing ``b, h > 0`` -> ``(5/6*A, 5/6*A)``.
    """
    from skyframe.core.sections_library import _W_SHAPES_IMPERIAL
    name = getattr(sec, "name", "")
    if name in W_SHAPE_TW_TF_IN and name in _W_SHAPES_IMPERIAL:
        tw, tf = W_SHAPE_TW_TF_IN[name]
        d, bf = _W_SHAPES_IMPERIAL[name][4], _W_SHAPES_IMPERIAL[name][5]
        return (d * _IN) * (tw * _IN), 5.0 / 3.0 * (bf * _IN) * (tf * _IN)
    if getattr(sec, "b", 0.0) > 0.0 and getattr(sec, "h", 0.0) > 0.0:
        return 5.0 / 6.0 * sec.A, 5.0 / 6.0 * sec.A
    return None, None


def resolved_shear_areas(sec) -> Optional[Tuple[Optional[float],
                                                Optional[float]]]:
    """(As2, As3) BEFORE modifiers for an enabled section, else None.

    Explicit values win; unset ones are auto-filled (see
    :func:`auto_shear_areas`); a direction left None is shear-rigid.
    """
    if not shear_enabled(sec):
        return None
    a2, a3 = getattr(sec, "As2", None), getattr(sec, "As3", None)
    if a2 is None or a3 is None:
        b2, b3 = auto_shear_areas(sec)
        a2 = b2 if a2 is None else a2
        a3 = b3 if a3 is None else a3
    return a2, a3


# a shear-rigid direction is modelled with Av = RIGID_SHEAR_FACTOR * A
# (phi = 12EI/(G Av L^2) ~ 1e-10 relative: Euler-Bernoulli to round-off)
RIGID_SHEAR_FACTOR = 1.0e10


def effective_shear_areas(sec) -> Optional[Tuple[float, float]]:
    """(Avy, Avz) with ``mod_As2``/``mod_As3`` applied, for the element.

    None when shear deformation is off or neither direction resolves.
    """
    res = resolved_shear_areas(sec)
    if res is None or (res[0] is None and res[1] is None):
        return None
    rigid = RIGID_SHEAR_FACTOR * sec.A * sec.mod_A
    a2 = (res[0] * getattr(sec, "mod_As2", 1.0)
          if res[0] is not None else rigid)
    a3 = (res[1] * getattr(sec, "mod_As3", 1.0)
          if res[1] is not None else rigid)
    return a2, a3


def frame_weight_mod(sec) -> float:
    return getattr(sec, "mod_weight", 1.0)


def frame_mass_mod(sec) -> float:
    return getattr(sec, "mod_mass", 1.0)


def validate_frame_mods(sec) -> None:
    """Raise ValueError on a bad shear-area / extra-modifier field."""
    for key in ("mod_As2", "mod_As3"):
        v = getattr(sec, key, 1.0)
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0):
            raise ValueError(f"Section {sec.name}: {key} must be a finite "
                             f"value > 0 (got {v!r})")
    for key in ("mod_mass", "mod_weight"):
        v = getattr(sec, key, 1.0)
        if not (isinstance(v, (int, float)) and math.isfinite(v)
                and v >= 0.0):
            raise ValueError(f"Section {sec.name}: {key} must be a finite "
                             f"value >= 0 (got {v!r})")
    for key in ("As2", "As3"):
        v = getattr(sec, key, None)
        if v is None:
            continue
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0):
            raise ValueError(f"Section {sec.name}: {key} must be null or a "
                             f"finite value > 0 m^2 (got {v!r})")
    if not isinstance(getattr(sec, "shear_deformation", False), bool):
        raise ValueError(f"Section {sec.name}: shear_deformation must be a "
                         "boolean")


def timoshenko_phi(E: float, G: float, I: float, Av: float,
                   L: float) -> float:
    """Shear-flexibility ratio ``phi = 12 E I / (G Av L^2)``."""
    return 12.0 * E * I / (G * Av * L * L)


# --------------------------------------------------------------------------- #
# shell sections
# --------------------------------------------------------------------------- #
def shell_mods_default(ssec) -> bool:
    """True when every new shell stiffness factor is 1.0 (legacy path)."""
    return all(getattr(ssec, k, 1.0) == 1.0 for k in SHELL_STIFF_KEYS)


def shell_weight_mod(ssec) -> float:
    return getattr(ssec, "weight", 1.0)


def shell_mass_mod(ssec) -> float:
    return getattr(ssec, "mass", 1.0)


def validate_shell_mods(ssec) -> None:
    for key in SHELL_STIFF_KEYS:
        v = getattr(ssec, key, 1.0)
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0):
            raise ValueError(f"Shell section {ssec.name}: {key} must be a "
                             f"finite value > 0 (got {v!r})")
    for key in ("mass", "weight"):
        v = getattr(ssec, key, 1.0)
        if not (isinstance(v, (int, float)) and math.isfinite(v)
                and v >= 0.0):
            raise ValueError(f"Shell section {ssec.name}: {key} must be a "
                             f"finite value >= 0 (got {v!r})")


def shell_uniform_factors(ssec) -> Optional[Tuple[float, float]]:
    """(membrane f, bending b) when an ElasticMembranePlateSection with the
    optional plate modifier reproduces the factors EXACTLY, else None.

    OpenSees' ``Ep_mod`` (probed) scales plate bending AND transverse shear
    together, so the uniform path needs ``f11 == f22 == f12``,
    ``m11 == m22 == m12 == v13 == v23``.
    """
    g = lambda k: getattr(ssec, k, 1.0)               # noqa: E731
    f = g("f11")
    b = g("m11")
    if g("f22") == f and g("f12") == f and all(
            g(k) == b for k in ("m22", "m12", "v13", "v23")):
        return f, b
    return None


def _qmat(E: float, nu: float, k11: float, k22: float,
          k12: float) -> np.ndarray:
    c = E / (1.0 - nu * nu)
    return np.array([[c * k11, c * nu * math.sqrt(k11 * k22), 0.0],
                     [c * nu * math.sqrt(k11 * k22), c * k22, 0.0],
                     [0.0, 0.0, c * (1.0 - nu) / 2.0 * k12]])


def shell_resultant_stiffness(E: float, nu: float, h: float, ssec
                              ) -> Tuple[np.ndarray, np.ndarray,
                                         float, float]:
    """Target (A 3x3, B 3x3, S13, S23) of the modified shell section.

    ``E`` already carries ``ssec.mod`` (and any cracked-slab scale).
    """
    g = lambda k: getattr(ssec, k, 1.0)               # noqa: E731
    A = h * _qmat(E, nu, g("f11"), g("f22"), g("f12"))
    B = h ** 3 / 12.0 * _qmat(E, nu, g("m11"), g("m22"), g("m12"))
    Gs = E / (2.0 * (1.0 + nu)) * 5.0 / 6.0 * h
    return A, B, g("v13") * Gs, g("v23") * Gs


def orthotropic_from_q(Q: np.ndarray) -> Tuple[float, float, float, float]:
    """(E1, E2, nu12, G12) of an orthotropic plane-stress matrix ``Q``."""
    q11, q22, q12, q66 = Q[0, 0], Q[1, 1], Q[0, 1], Q[2, 2]
    nu12 = q12 / q22
    nu21 = q12 / q11
    d = 1.0 - nu12 * nu21
    return q11 * d, q22 * d, nu12, q66


def skin_core_layers(A: np.ndarray, B: np.ndarray, S13: float,
                     S23: float) -> Tuple[float, List[tuple]]:
    """Exact 3-layer (skin / core / skin) LayeredShell equivalent.

    OpenSees ``LayeredShell`` integrates each layer at its MID-plane
    (probed: 3 equal isotropic layers are 12.5% stiffer in bending than the
    continuum) and sums the transverse shear as ``sum(G_k t_k)`` (no 5/6;
    probed against ElasticMembranePlateSection).  With three layers of
    equal thickness ``t`` the mid-planes are at ``-t, 0, +t``, so

        membrane = t*(2 D_s + D_c),   bending = 2 t^3 D_s,
        shear_a  = 3 t G_a.

    Choosing ``t^2 = 2 * max eig(A^-1 B)`` keeps ``D_c = (A - B/t^2)/t``
    positive definite; ``D_s = B/(2 t^3)``.  Membrane-bending coupling is
    zero by symmetry.  Returns ``(t, [(E1, E2, nu12, G12, G13, G23) for
    skin, core])``.
    """
    lam = float(max(np.real(np.linalg.eigvals(np.linalg.solve(A, B)))))
    t = math.sqrt(2.0 * lam)
    Ds = B / (2.0 * t ** 3)
    Dc = (A - B / t ** 2) / t
    g13, g23 = S13 / (3.0 * t), S23 / (3.0 * t)
    out = []
    for D in (Ds, Dc):
        E1, E2, nu12, G12 = orthotropic_from_q(D)
        out.append((E1, E2, nu12, G12, g13, g23))
    return t, out
