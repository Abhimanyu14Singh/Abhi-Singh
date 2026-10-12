"""Anisotropic shell materials, the shell material angle, laminates at any
angle and layered shells with any layer material (Wave C).

CONTRACT "Anisotropic shells, material angle, Section Designer
moment-curvature, layered shells with any material, results CSV folder".

Material (``Material.symmetry == "anisotropic"``)
    ``Material.aniso = {"Q11", "Q22", "Q12", "Q16", "Q26", "Q66", "G13",
    "G23"}`` (kPa): the user-defined plane-stress constitutive matrix in the
    MATERIAL axes 1 / 2 (engineering shear strain ``gamma12``)::

        {s11}   [Q11 Q12 Q16] {e11}
        {s22} = [Q12 Q22 Q26] {e22}         Q positive definite
        {s12}   [Q16 Q26 Q66] {g12}

    plus the transverse shear moduli G13 / G23.  ``Material.E`` / ``nu``
    stay the frame (1-D) values; ``Material.G`` = Q66.

Material angle
    ``Material.material_angle`` (deg, default for every shell using the
    material) and ``ShellSection.material_angle`` (deg, overrides the
    material's): the angle from the SHELL ELEMENT local axis 1 to the
    material axis 1, positive about the element local axis 3 (1 -> 2).
    The in-plane matrix in the element axes is

        Qbar = T^T Q T,   T = [[c^2, s^2, cs], [s^2, c^2, -cs],
                               [-2cs, 2cs, c^2 - s^2]]

    (``T`` maps element strains to material strains); at 90 degrees this is
    exactly Q with 1 <-> 2 swapped.  Transverse shear rotates as
    ``G13bar = G13 c^2 + G23 s^2``, ``G23bar = G13 s^2 + G23 c^2``; the
    cross term ``(G13 - G23) cs`` is NOT carried (OpenSees plate fibers
    have uncoupled transverse shear) - zero at 0 / 90 degrees and for
    G13 = G23.

Engine realisation (exact for the in-plane / bending stiffness)
    OpenSees has no anisotropic plate material, so a target membrane
    resultant ``A`` (3x3), a bending second moment ``D`` (3x3) about the
    layer's own centre and the shear resultants are realised by an
    11-slice PALINDROMIC ``LayeredShell`` stack (:func:`slices`) of
    ``ElasticOrthotropic + PlateFiber`` slices (carry the 11 / 22 / 12 / 66
    entries and the transverse shear) and ``PlateRebar`` fibre slices at
    30 / 60 degrees (rank-one ``E m m^T``, ``m = [c^2, s^2, cs]`` - probed -
    carry the normal-shear coupling 16 / 26).  Mirror slices share their
    parameters, so the stack has no first moment about its own centre and
    is orientation-independent; LayeredShell integrates each slice at its
    mid-plane (``A = sum t_k Q_k``, ``D = sum t_k z_k^2 Q_k``), so the
    two-equation (membrane, second moment) solve per entry is exact.
    Individual slices may carry negative parameters; only the sums are
    physical.

Laminates (``ShellSection.laminate``)
    Layer ``angle`` may be ANY angle (deg; Wave B allowed 0 / 90 only) and
    a layer material may be anisotropic; the section ``material_angle``
    rotates the whole lay-up.  The lay-up must still be symmetric (B = 0).

Layered shells (``ShellSection.layered``)
    Layer kinds beyond "concrete" / "steel" (:data:`NEW_LAYER_KINDS`):

    * ``"elastic"`` - linear elastic layer of ANY material (isotropic,
      orthotropic or anisotropic) at ``angle`` (deg);
    * ``"orthotropic"`` - the same, the material must be orthotropic or
      anisotropic;
    * ``"timber"`` - the same, the material must be orthotropic (e.g. the
      EN 338 C24 / GL24h presets);
    * ``"user"`` - the material's user ``stress_strain`` law
      (:mod:`skyframe.core.stress_strain`) as a smeared uniaxial layer
      along ``angle`` (``PlateRebar``), elastic modulus = the law's initial
      slope in linear runs.

    A section with at least one such layer is analysed as the real stack
    in EVERY run (linear runs included, exact elastic composite), not as a
    homogeneous section of ``sum(t)``; sections with only concrete / steel
    layers keep the legacy behaviour byte-identically.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

ANISO_KEYS = ("Q11", "Q22", "Q12", "Q16", "Q26", "Q66", "G13", "G23")
NEW_LAYER_KINDS = ("elastic", "orthotropic", "timber", "user")
FIBRE_ANGLES = (30.0, 60.0)          # deg - coupling carriers
N_SLICES = 11                        # palindromic scaffold
_ZERO = 1e-12


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


# --------------------------------------------------------------------------- #
# material
# --------------------------------------------------------------------------- #
def is_aniso(mat) -> bool:
    return getattr(mat, "symmetry", "isotropic") == "anisotropic"


def is_ortho_like(mat) -> bool:
    return getattr(mat, "symmetry", "isotropic") in ("orthotropic",
                                                       "anisotropic")


def normalize_aniso(a) -> Optional[dict]:
    if a is None:
        return None
    if not isinstance(a, dict):
        raise ValueError("aniso must be an object {Q11 .. Q66, G13, G23}")
    return {k: float(a[k]) for k in ANISO_KEYS if k in a}


def aniso_q(a: dict) -> np.ndarray:
    return np.array([[a["Q11"], a["Q12"], a["Q16"]],
                     [a["Q12"], a["Q22"], a["Q26"]],
                     [a["Q16"], a["Q26"], a["Q66"]]], dtype=float)


def validate_material(mat) -> None:
    """ValueError on a malformed anisotropic material / material angle."""
    a = getattr(mat, "aniso", None)
    ang = getattr(mat, "material_angle", None)
    if ang is not None and not _finite(ang):
        raise ValueError(f"Material {mat.name!r}: material_angle must be a "
                         f"finite angle in degrees (got {ang!r})")
    if not is_aniso(mat):
        if a is not None:
            raise ValueError(f"Material {mat.name!r}: 'aniso' constants need "
                             "symmetry 'anisotropic'")
        return
    if getattr(mat, "ortho", None) is not None:
        raise ValueError(f"Material {mat.name!r}: an anisotropic material "
                         "takes 'aniso', not 'ortho'")
    if not isinstance(a, dict):
        raise ValueError(f"Material {mat.name!r}: symmetry 'anisotropic' "
                         "needs aniso = {Q11, Q22, Q12, Q16, Q26, Q66, G13, "
                         "G23} (kPa)")
    unknown = set(a) - set(ANISO_KEYS)
    if unknown:
        raise ValueError(f"Material {mat.name!r}: unknown aniso key(s) "
                         f"{sorted(unknown)} (allowed: {list(ANISO_KEYS)})")
    for k in ANISO_KEYS:
        v = a.get(k)
        if not _finite(v):
            raise ValueError(f"Material {mat.name!r}: aniso {k} must be a "
                             f"finite value in kPa (got {v!r})")
    for k in ("Q11", "Q22", "Q66", "G13", "G23"):
        if a[k] <= 0.0:
            raise ValueError(f"Material {mat.name!r}: aniso {k} must be "
                             "> 0 kPa")
    ev = np.linalg.eigvalsh(aniso_q(a))
    if ev.min() <= 1e-12 * ev.max():
        raise ValueError(f"Material {mat.name!r}: the anisotropic matrix Q "
                         "must be positive definite")


def material_G(mat) -> Optional[float]:
    """Frame shear modulus of an anisotropic material (Q66) or None."""
    a = getattr(mat, "aniso", None)
    if is_aniso(mat) and a is not None and "Q66" in a:
        return float(a["Q66"])
    return None


def t_matrix(theta_deg: float) -> np.ndarray:
    """Element-strain -> material-strain transformation (engineering
    shear), material axis 1 at ``theta`` from element axis 1."""
    t = math.radians(float(theta_deg))
    c, s = math.cos(t), math.sin(t)
    return np.array([[c * c, s * s, c * s],
                     [s * s, c * c, -c * s],
                     [-2.0 * c * s, 2.0 * c * s, c * c - s * s]])


def rotate_q(Q: np.ndarray, theta_deg: float) -> np.ndarray:
    """Qbar = T^T Q T (material axes -> element axes)."""
    if _is_multiple(theta_deg, 360.0):
        return np.array(Q, dtype=float)
    T = t_matrix(theta_deg)
    return T.T @ np.asarray(Q, dtype=float) @ T


def rotate_shear(g13: float, g23: float, theta_deg: float
                 ) -> Tuple[float, float]:
    """Diagonal transverse shear moduli in the element axes (the cross
    term (G13 - G23) cs is dropped - module doc)."""
    if _is_multiple(theta_deg, 180.0):
        return g13, g23
    t = math.radians(float(theta_deg))
    c2, s2 = math.cos(t) ** 2, math.sin(t) ** 2
    if _is_multiple(theta_deg - 90.0, 180.0):
        return g23, g13
    return g13 * c2 + g23 * s2, g13 * s2 + g23 * c2


def _is_multiple(a: float, period: float) -> bool:
    r = math.fmod(float(a), period)
    return abs(r) < 1e-9 or abs(abs(r) - period) < 1e-9


def material_q(mat, theta_deg: float = 0.0
               ) -> Tuple[np.ndarray, float, float]:
    """(Q 3x3, G13, G23) of a material in the element axes after rotating
    its material axes by ``theta`` (isotropic / orthotropic /
    anisotropic)."""
    from skyframe.core import ortho as _ortho
    if is_aniso(mat):
        a = mat.aniso
        Q, g13, g23 = aniso_q(a), float(a["G13"]), float(a["G23"])
    else:
        c = _ortho.constants(mat)
        Q, g13, g23 = _ortho.q_matrix(c), c["G13"], c["G23"]
    Qb = rotate_q(Q, theta_deg)
    g = rotate_shear(g13, g23, theta_deg)
    return Qb, g[0], g[1]


def coupled(M: np.ndarray, rel: float = 1e-12) -> bool:
    """True when a 3x3 in-plane matrix has normal-shear coupling."""
    scale = max(abs(M).max(), 1e-300)
    return abs(M[0, 2]) > rel * scale or abs(M[1, 2]) > rel * scale


# --------------------------------------------------------------------------- #
# shell sections
# --------------------------------------------------------------------------- #
def section_angle(ssec, mat) -> float:
    """Effective material angle (deg) of a shell section."""
    a = getattr(ssec, "material_angle", None)
    if a is None:
        a = getattr(mat, "material_angle", None) if mat is not None else None
    return float(a or 0.0)


def _lam_general(lam, materials) -> bool:
    for la in lam.get("layers") or []:
        m = materials.get(la.get("material")) if materials else None
        ang = float(la.get("angle", 0.0) or 0.0)
        if m is not None and is_aniso(m):
            return True
        if not (_is_multiple(ang, 90.0)):
            return True
    return False


def needs_general(ssec, mat, materials=None) -> bool:
    """True when the section needs this module's engine path: an
    anisotropic material, a non-zero material angle on an orthotropic /
    anisotropic material or laminate, a laminate layer at an angle other
    than 0 / 90 or of an anisotropic material, or a layered section with
    one of :data:`NEW_LAYER_KINDS`.  False for every legacy section."""
    if layered_general(ssec):
        return True
    lam = getattr(ssec, "laminate", None)
    ang = section_angle(ssec, mat)
    if lam is not None:
        return (not _is_multiple(ang, 360.0)) or _lam_general(lam,
                                                               materials)
    if mat is None:
        return False
    if is_aniso(mat):
        return True
    return is_ortho_like(mat) and not _is_multiple(ang, 360.0)


def laminate_abd(lam: dict, materials, extra_angle: float = 0.0):
    """(A, B, D, (S13, S23)) of a laminate at any layer angle (bottom ->
    top), continuum classical lamination theory about the mid-surface;
    shear ``5/6 sum t_k G_k`` (as :func:`skyframe.core.ortho.
    laminate_resultants`, which this reproduces for 0 / 90 layers)."""
    layers = lam["layers"]
    h = sum(float(la["t"]) for la in layers)
    z = -h / 2.0
    A = np.zeros((3, 3))
    B = np.zeros((3, 3))
    D = np.zeros((3, 3))
    s13 = s23 = 0.0
    for la in layers:
        t = float(la["t"])
        Q, g13, g23 = material_q(materials[la["material"]],
                                 float(la.get("angle", 0.0) or 0.0)
                                 + extra_angle)
        zb = z + t / 2.0
        A += t * Q
        B += t * zb * Q
        D += (t ** 3 / 12.0 + t * zb * zb) * Q
        s13 += t * g13
        s23 += t * g23
        z += t
    return A, B, D, (5.0 / 6.0 * s13, 5.0 / 6.0 * s23)


def base_resultants(ssec, mat, materials=None):
    """(A, D, S13, S23) per unit width (element axes) before modifiers."""
    ang = section_angle(ssec, mat)
    lam = getattr(ssec, "laminate", None)
    if lam is not None:
        A, _, D, (s13, s23) = laminate_abd(lam, materials, ang)
        return A, D, s13, s23
    h = float(ssec.thickness)
    Q, g13, g23 = material_q(mat, ang)
    return h * Q, h ** 3 / 12.0 * Q, 5.0 / 6.0 * g13 * h, 5.0 / 6.0 * g23 * h


def apply_factors(M: np.ndarray, k11: float, k22: float, k12: float
                  ) -> np.ndarray:
    """ETABS f / m modifiers as the congruence diag(sqrt k) M diag(sqrt k)
    (Wave B rule for 11 / 22 / 12 / 66 entries; the coupling entries take
    sqrt(k11 k12) / sqrt(k22 k12))."""
    r = np.sqrt(np.array([k11, k22, k12], dtype=float))
    return np.asarray(M, dtype=float) * np.outer(r, r)


def validate_section(ssec, materials) -> None:
    """Section-level checks of the Wave C fields (material_angle)."""
    a = getattr(ssec, "material_angle", None)
    if a is None:
        return
    if not _finite(a):
        raise ValueError(f"Shell section {ssec.name}: material_angle must "
                         f"be a finite angle in degrees (got {a!r})")
    from skyframe.core import slab_props as _slab
    if _slab.slab_type(ssec) is not None:
        raise ValueError(f"Shell section {ssec.name}: material_angle is not "
                         "supported on ribbed / waffle / deck / drop slab "
                         "types")
    if getattr(ssec, "layered", None):
        raise ValueError(f"Shell section {ssec.name}: a layered section "
                         "sets the angle per layer (layer 'angle'), not "
                         "material_angle")


# --------------------------------------------------------------------------- #
# layered shells with any material
# --------------------------------------------------------------------------- #
def layered_general(ssec) -> bool:
    lay = getattr(ssec, "layered", None)
    if not lay or not isinstance(lay, dict):
        return False
    return any(isinstance(la, dict) and la.get("kind") in NEW_LAYER_KINDS
               for la in lay.get("layers") or [])


def validate_layer(ssec_name: str, k: int, la: dict, materials) -> None:
    """Checks for a layer of one of :data:`NEW_LAYER_KINDS` (the generic
    t / material / key checks are done by the model)."""
    kind = la.get("kind")
    if kind not in NEW_LAYER_KINDS:
        return
    mat = materials.get(la.get("material"))
    pre = f"Shell section {ssec_name}: layer {k}"
    ang = la.get("angle", 0.0)
    if not _finite(ang):
        raise ValueError(f"{pre}: angle must be a finite angle in degrees")
    if mat is None:
        return
    sym = getattr(mat, "symmetry", "isotropic")
    if kind == "orthotropic" and sym not in ("orthotropic", "anisotropic"):
        raise ValueError(f"{pre}: kind 'orthotropic' needs an orthotropic "
                         f"or anisotropic material ({mat.name!r} is {sym})")
    if kind == "timber" and sym != "orthotropic":
        raise ValueError(f"{pre}: kind 'timber' needs an orthotropic "
                         f"material such as the C24 / GL24h presets "
                         f"({mat.name!r} is {sym})")
    if kind == "elastic" and sym == "uniaxial":
        raise ValueError(f"{pre}: kind 'elastic' needs an isotropic, "
                         f"orthotropic or anisotropic material ({mat.name!r} "
                         "is uniaxial - use kind 'steel' or 'user')")
    if kind == "user":
        from skyframe.core import stress_strain as _ss
        law = _ss.material_law(mat)
        if law is None:
            raise ValueError(f"{pre}: kind 'user' needs a material with a "
                             f"stress_strain law ({mat.name!r} has none)")


def user_law(mat, E=None):
    from skyframe.core import stress_strain as _ss
    return _ss.material_law(mat, E=E)


def law_initial_modulus(law) -> float:
    """Initial slope of a uniaxial law (tension side if it has one, else
    compression), by a small-strain secant."""
    for e in (1e-7, -1e-7):
        s = law.stress(e)
        if abs(s) > 0.0:
            return s / e
    return float(law.params.get("E", 0.0))


def layer_elastic(la: dict, materials, mod: float = 1.0
                  ) -> Tuple[np.ndarray, float, float]:
    """(Q 3x3, G13, G23) of ONE layer of any kind in the element axes, the
    initial elastic stiffness the engine gives it (concrete = isotropic
    E, nu; steel = PlateRebar rank-one E m m^T without transverse shear;
    elastic / orthotropic / timber = the rotated material; user = the
    law's initial slope as a rank-one fibre)."""
    mat = materials[la["material"]]
    kind = la["kind"]
    ang = float(la.get("angle", 0.0) or 0.0)
    if kind in ("elastic", "orthotropic", "timber"):
        Q, g13, g23 = material_q(mat, ang)
        return mod * Q, mod * g13, mod * g23
    if kind == "concrete":
        E = mat.E * mod
        nu = mat.nu
        q = E / (1.0 - nu * nu)
        G = E / (2.0 * (1.0 + nu))
        return (np.array([[q, nu * q, 0.0], [nu * q, q, 0.0],
                          [0.0, 0.0, G]]), G, G)
    if kind == "user":
        E = law_initial_modulus(user_law(mat, E=mat.E * mod))
    else:
        E = mat.E * mod
    t = math.radians(ang)
    m = np.array([math.cos(t) ** 2, math.sin(t) ** 2,
                  math.cos(t) * math.sin(t)])
    return E * np.outer(m, m), 0.0, 0.0


def layered_abd(ssec, materials, mod: float = 1.0):
    """(A, B, D, (S13, S23)) of a layered stack (layers bottom -> top,
    continuum integration, shear ``sum t G`` - the LayeredShell rule)."""
    layers = ssec.layered["layers"]
    h = sum(float(la["t"]) for la in layers)
    z = -h / 2.0
    A = np.zeros((3, 3))
    B = np.zeros((3, 3))
    D = np.zeros((3, 3))
    s13 = s23 = 0.0
    for la in layers:
        t = float(la["t"])
        Q, g13, g23 = layer_elastic(la, materials, mod)
        zb = z + t / 2.0
        A += t * Q
        B += t * zb * Q
        D += (t ** 3 / 12.0 + t * zb * zb) * Q
        s13 += t * g13
        s23 += t * g23
        z += t
    return A, B, D, (s13, s23)


# --------------------------------------------------------------------------- #
# the exact 11-slice realisation
# --------------------------------------------------------------------------- #
def _fibre_m(theta_deg: float) -> np.ndarray:
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([c * c, s * s, c * s])


def slices(A_t: np.ndarray, D_t: np.ndarray, S13: float, S23: float,
           thickness: float) -> List[Tuple[float, tuple]]:
    """Exact palindromic 11-slice stack of total ``thickness`` whose
    membrane resultant is ``A_t`` and whose second moment about its own
    centre is ``D_t`` (both 3x3, element axes), transverse shear
    ``sum t G = (S13, S23)``.  Returns ``[(t_k, spec), ...]`` bottom ->
    top with ``spec = ("ortho", E1, E2, nu12, G12, G13, G23)`` or
    ``("fibre", E, angle_deg)``.

    Layout (slice index k, centre z_k = (k - 5) tau, tau = thickness/11):
    orthotropic slices at k = 0, 5, 10; fibres at 30 deg on k = 1, 3, 7,
    9 and at 60 deg on k = 2, 4, 6, 8; mirror slices share parameters.
    Per coupling entry e in (16, 26) the four fibre moduli solve
    ``sum 2 tau E_p c_e = A_e`` / ``sum 2 tau z_p^2 E_p c_e = D_e``; the
    orthotropic slices then carry the remaining 11 / 22 / 12 / 66 entries
    (outer ``q_o = D_e / (2 tau (5 tau)^2)``, centre ``q_c = (A_e - 2 tau
    q_o) / tau``) and the shear ``G = S / (3 tau)``.
    """
    from skyframe.core.modifiers import orthotropic_from_q
    tau = float(thickness) / N_SLICES
    A_t = np.asarray(A_t, dtype=float)
    D_t = np.asarray(D_t, dtype=float)
    # fibre pairs: (angle, |z| in tau)
    pairs = [(FIBRE_ANGLES[0], 4.0), (FIBRE_ANGLES[0], 2.0),
             (FIBRE_ANGLES[1], 3.0), (FIBRE_ANGLES[1], 1.0)]
    M = np.zeros((4, 4))
    rhs = np.array([A_t[0, 2], A_t[1, 2], D_t[0, 2], D_t[1, 2]])
    for j, (ang, zz) in enumerate(pairs):
        m = _fibre_m(ang)
        c16, c26 = m[0] * m[2], m[1] * m[2]
        w_a, w_d = 2.0 * tau, 2.0 * tau * (zz * tau) ** 2
        M[:, j] = [w_a * c16, w_a * c26, w_d * c16, w_d * c26]
    E_f = np.linalg.solve(M, rhs) if coupled(A_t) or coupled(D_t) \
        else np.zeros(4)
    A_r = A_t.copy()
    D_r = D_t.copy()
    for (ang, zz), E in zip(pairs, E_f):
        mm = np.outer(_fibre_m(ang), _fibre_m(ang)) * E
        A_r -= 2.0 * tau * mm
        D_r -= 2.0 * tau * (zz * tau) ** 2 * mm
    a = 5.0 * tau
    Qo = D_r / (2.0 * tau * a * a)
    Qc = (A_r - 2.0 * tau * Qo) / tau
    g13, g23 = S13 / (3.0 * tau), S23 / (3.0 * tau)
    specs = {}
    for key, Q in (("o", Qo), ("c", Qc)):
        if abs(Q[0, 0]) < _ZERO * max(abs(Q).max(), 1.0) or \
                abs(Q[1, 1]) < _ZERO * max(abs(Q).max(), 1.0):
            raise ValueError("anisotropic shell realisation: degenerate "
                             "slice stiffness (Q11 or Q22 = 0)")
        E1, E2, nu12, G12 = orthotropic_from_q(Q)
        specs[key] = ("ortho", E1, E2, nu12, G12, g13, g23)
    fib = {}
    for (ang, zz), E in zip(pairs, E_f):
        fib[int(zz)] = ("fibre", float(E), ang)
    order = [specs["o"], fib[4], fib[3], fib[2], fib[1], specs["c"],
             fib[1], fib[2], fib[3], fib[4], specs["o"]]
    return [(tau, s) for s in order]


def layer_stack(Q: np.ndarray, g13: float, g23: float, t: float,
                fibre: Optional[Tuple[float, float]] = None
                ) -> List[Tuple[float, tuple]]:
    """Exact slice stack of ONE homogeneous layer of thickness ``t``
    (membrane ``t Q``, continuum second moment ``t^3/12 Q`` about its own
    centre, shear ``t G``):

    * ``fibre = (E, angle)`` (steel / user layers): 3 PlateRebar slices
      ``1.125 E | 0.75 E | 1.125 E`` (``2 tau (1.125) + tau (0.75) = t``,
      ``2 tau tau^2 1.125 = t^3 / 12`` with ``tau = t / 3``);
    * no normal-shear coupling: 3 orthotropic slices ``1.125 Q | 0.75 Q |
      1.125 Q`` (same weights), shear G on each;
    * coupled: the 11-slice :func:`slices` stack.
    """
    tau = t / 3.0
    if fibre is not None:
        E, ang = fibre
        return [(tau, ("fibre", 1.125 * E, ang)),
                (tau, ("fibre", 0.75 * E, ang)),
                (tau, ("fibre", 1.125 * E, ang))]
    if not coupled(Q):
        from skyframe.core.modifiers import orthotropic_from_q
        out = []
        for w in (1.125, 0.75, 1.125):
            E1, E2, nu12, G12 = orthotropic_from_q(w * np.asarray(Q))
            out.append((tau, ("ortho", E1, E2, nu12, G12, g13, g23)))
        return out
    return slices(t * Q, t ** 3 / 12.0 * Q, t * g13, t * g23, t)


def layer_fibre(la: dict, materials, mod: float = 1.0
                ) -> Optional[Tuple[float, float]]:
    """(E, angle) of a rank-one (steel / user) layer, else None."""
    kind = la["kind"]
    if kind not in ("steel", "user"):
        return None
    mat = materials[la["material"]]
    if kind == "user":
        E = law_initial_modulus(user_law(mat, E=mat.E * mod))
    else:
        E = mat.E * mod
    return E, float(la.get("angle", 0.0) or 0.0)


def realised(sl: List[Tuple[float, tuple]]
             ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """(A, B, D, S13, S23) a slice stack gives under the LayeredShell
    mid-plane rule (self-check helper; z about the stack centre)."""
    h = sum(t for t, _ in sl)
    z = -h / 2.0
    A = np.zeros((3, 3))
    B = np.zeros((3, 3))
    D = np.zeros((3, 3))
    s13 = s23 = 0.0
    for t, sp in sl:
        zc = z + t / 2.0
        if sp[0] == "ortho":
            _, E1, E2, nu12, G12, G13, G23 = sp
            nu21 = nu12 * E2 / E1
            d = 1.0 - nu12 * nu21
            Q = np.array([[E1 / d, nu12 * E2 / d, 0.0],
                          [nu12 * E2 / d, E2 / d, 0.0], [0.0, 0.0, G12]])
            s13 += t * G13
            s23 += t * G23
        else:
            m = _fibre_m(sp[2])
            Q = sp[1] * np.outer(m, m)
        A += t * Q
        B += t * zc * Q
        D += t * zc * zc * Q
        z += t
    return A, B, D, s13, s23


def summary(ssec, mat, materials) -> Dict[str, object]:
    """JSON summary of the element-axes stiffness (API / dialog helper)."""
    A, D, s13, s23 = base_resultants(ssec, mat, materials)
    return {"angle": section_angle(ssec, mat),
            "A": A.tolist(), "D": D.tolist(), "S13": s13, "S23": s23}


def layer_stress_map(ssec, materials, layer: int, T_i=None) -> np.ndarray:
    """(3, 8) map from resultants (N11 N22 N12 M11 M22 M12 V13 V23) to the
    stress triple at the centre of layer ``layer`` of a layered section
    with a new layer kind - EXACT for linear runs (the engine solves the
    real elastic stack): ``[eps; kappa] = [[A, B], [B, D]]^-1 [N; M]``
    (continuum, layers bottom -> top, ``mod`` applied), ``sigma = Q_layer
    (eps + z kappa)``.  ``T_i`` (8 x 8, element -> output resultants)
    rotates both sides when results are requested in other axes."""
    lays = ssec.layered["layers"]
    if layer >= len(lays):
        return np.full((3, 8), np.nan)
    mod = float(getattr(ssec, "mod", 1.0) or 1.0)
    A, B, D, _ = layered_abd(ssec, materials, mod)
    K = np.block([[A, B], [B, D]])
    C = np.linalg.pinv(K)
    h = sum(float(la["t"]) for la in lays)
    z = -h / 2.0 + sum(float(la["t"]) for la in lays[:layer]) \
        + float(lays[layer]["t"]) / 2.0
    Q, _, _ = layer_elastic(lays[layer], materials, mod)
    L = np.zeros((3, 8))
    L[:, 0:6] = Q @ (C[0:3, :] + z * C[3:6, :])
    if T_i is not None:
        Ti = np.asarray(T_i, dtype=float)
        L = Ti[0:3, 0:3] @ L @ np.linalg.inv(Ti)
    return L


# --------------------------------------------------------------------------- #
# Interactive Database Editing (one-line hook in core.props_tables)
# --------------------------------------------------------------------------- #
def _aniso_get(k):
    return lambda o, d: (o.get("aniso") or {}).get(k)


def _aniso_put(k):
    def put(o, v, d):
        aa = dict(o.get("aniso") or {})
        if v is None:
            aa.pop(k, None)
        else:
            aa[k] = float(v)
        if aa:
            o["aniso"] = aa
        else:
            o.pop("aniso", None)
    return put


def _angle_put(o, v, d):
    if v is None or v == "":
        o.pop("material_angle", None)
    else:
        o["material_angle"] = float(v)


def extend_catalogue(T) -> None:
    """Materials: Material Angle + the anisotropic Q11..G23 columns;
    Shell Sections: Material Angle (blank = unset, key removed)."""
    from .modeltables import Col
    mt = next(t for t in T if t.key == "materials")
    i = next((k for k, c in enumerate(mt.cols) if c.key == "ortho_G23"),
             len(mt.cols) - 1) + 1
    mt.cols = mt.cols[:i] + [
        Col("mat_angle", "Material Angle", "number", "angle_deg",
            optional=True, get=lambda o, d: o.get("material_angle"),
            put=_angle_put)] + [
        Col(f"aniso_{k}", k, "number", "modulus", optional=True,
            get=_aniso_get(k), put=_aniso_put(k)) for k in ANISO_KEYS] + \
        mt.cols[i:]
    ss = next(t for t in T if t.key == "shell_sections")
    ss.cols = ss.cols + [
        Col("material_angle", "Material Angle", "number", "angle_deg",
            optional=True, get=lambda o, d: o.get("material_angle"),
            put=_angle_put)]


# --------------------------------------------------------------------------- #
# serialisation / validation hub (one-line hooks in core.props_ext)
# --------------------------------------------------------------------------- #
def material_to_dict(d: dict) -> dict:
    """Drop the Wave C material keys while unset (byte-identical saves)."""
    for k in ("aniso", "material_angle"):
        if d.get(k) is None:
            d.pop(k, None)
    return d


def shell_section_to_dict(d: dict) -> dict:
    if d.get("material_angle") is None:
        d.pop("material_angle", None)
    return d


def model_from_dict(mdl, d: dict) -> None:
    """Read ``ShellSection.material_angle`` (materials are read by
    ``material_from_dict``; layer dicts are kept verbatim)."""
    for name, sd in (d.get("shell_sections") or {}).items():
        ss = mdl.shell_sections.get(name)
        if ss is None or not isinstance(sd, dict):
            continue
        if sd.get("material_angle") is not None:
            ss.material_angle = float(sd["material_angle"])


def validate_model(model) -> None:
    for mat in model.materials.values():
        validate_material(mat)
    for ss in model.shell_sections.values():
        validate_section(ss, model.materials)
        smat = model.materials.get(ss.material)
        if smat is not None and is_aniso(smat) and \
                getattr(ss, "layered", None):
            raise ValueError(f"Shell section {ss.name}: a layered section "
                             "cannot use the anisotropic material directly "
                             "(use it in a layer)")
        lay = getattr(ss, "layered", None)
        if lay and isinstance(lay, dict):
            for k, la in enumerate(lay.get("layers") or []):
                if isinstance(la, dict):
                    validate_layer(ss.name, k, la, model.materials)
