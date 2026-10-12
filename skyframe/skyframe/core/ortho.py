"""Orthotropic materials and laminated (CLT) shell sections (Wave B).

CONTRACT "Orthotropic materials, slab / deck property types, cold-formed and
composite sections, named line / area springs".

Material (``Material.symmetry == "orthotropic"``)
    ``Material.E``  = E1 (kPa), ``Material.nu`` = nu12 and
    ``Material.ortho = {"E2", "G12", "G13", "G23"}`` (kPa).  Material axis 1
    / 2 coincide with the SHELL ELEMENT local axes 1 / 2 (no material angle;
    a laminate layer may be turned by 90 degrees).  Frame members made of an
    orthotropic material use E = E1 and G = G12 (``Material.G``).

    Plane-stress stiffness ``Q`` (nu21 = nu12 E2 / E1)::

        Q11 = E1 / (1 - nu12 nu21)   Q22 = E2 / (1 - nu12 nu21)
        Q12 = nu12 E2 / (1 - nu12 nu21)   Q66 = G12

    Homogeneous orthotropic shell of thickness h::

        A = h Q,  D = h^3 / 12 Q,  S13 = 5/6 G13 h,  S23 = 5/6 G23 h

    which the engine realises EXACTLY with the existing three-layer
    ``ElasticOrthotropic`` + ``PlateFiber`` ``LayeredShell``
    (:func:`skyframe.core.modifiers.skin_core_layers`).

Laminate (``ShellSection.laminate``)::

    {"layers": [{"t": m, "material": name, "angle": 0 | 90}, ...]}

    listed from the BOTTOM face up.  Classical lamination theory about the
    mid-surface z = 0: ``A = sum t_k Q_k``, ``B = sum t_k zbar_k Q_k``,
    ``D = sum (t_k^3 / 12 + t_k zbar_k^2) Q_k``, shear ``S13 = 5/6 sum t_k
    G13_k`` (``S23`` alike; a 90-degree layer swaps 1 <-> 2 and G13 <->
    G23).  The coupling ``B`` must vanish (symmetric lay-up) - an
    unsymmetric lay-up is rejected.  Isotropic layer materials are allowed
    (E1 = E2 = E, G12 = G13 = G23 = G).

Presets (:data:`TIMBER_PRESETS`, :func:`clt_preset`): EN 338 C24 and
EN 14080 GL24h glulam (mean stiffness, EN 338:2016 Table 1 / EN 14080:2013
Table 5) and the usual CLT idealisation (rolling shear modulus 50 MPa,
transverse E90 of the board).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

ORTHO_KEYS = ("E2", "G12", "G13", "G23")

#: EN 338 / EN 14080 mean values (kPa) + density (kN/m^3 weight, mean)
TIMBER_PRESETS: Dict[str, dict] = {
    "C24": {"E1": 11.0e6, "E2": 0.37e6, "nu12": 0.30, "G12": 0.69e6,
            "G13": 0.69e6, "G23": 0.05e6, "unit_weight": 4.20,
            "notes": "EN 338:2016 C24 softwood (E0,mean 11 GPa, E90,mean "
                     "0.37 GPa, Gmean 0.69 GPa, rolling shear 0.05 GPa, "
                     "rho_mean 420 kg/m3)"},
    "GL24h": {"E1": 11.5e6, "E2": 0.30e6, "nu12": 0.30, "G12": 0.65e6,
              "G13": 0.65e6, "G23": 0.065e6, "unit_weight": 4.20,
              "notes": "EN 14080:2013 GL24h glulam (E0,mean 11.5 GPa, "
                       "E90,mean 0.30 GPa, Gmean 0.65 GPa, rolling shear "
                       "0.065 GPa, rho_mean 420 kg/m3)"},
}

#: CLT lay-ups: name -> (preset material, [layer thickness m ...]) with
#: alternating 0 / 90 layers starting with 0 (outer layers along local 1)
CLT_LAYUPS: Dict[str, Tuple[str, List[float]]] = {
    "CLT 100 L3s": ("C24", [0.03, 0.04, 0.03]),
    "CLT 120 L3s": ("C24", [0.04, 0.04, 0.04]),
    "CLT 160 L5s": ("C24", [0.04, 0.02, 0.04, 0.02, 0.04]),
    "CLT 200 L5s": ("C24", [0.04, 0.04, 0.04, 0.04, 0.04]),
}


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


# --------------------------------------------------------------------------- #
# material
# --------------------------------------------------------------------------- #
def is_ortho(mat) -> bool:
    return getattr(mat, "symmetry", "isotropic") == "orthotropic"


def constants(mat) -> Dict[str, float]:
    """E1, E2, nu12, G12, G13, G23 of a material (isotropic -> equal)."""
    if is_ortho(mat):
        o = mat.ortho or {}
        return {"E1": float(mat.E), "E2": float(o["E2"]),
                "nu12": float(mat.nu), "G12": float(o["G12"]),
                "G13": float(o["G13"]), "G23": float(o["G23"])}
    G = mat.E / (2.0 * (1.0 + mat.nu))
    return {"E1": float(mat.E), "E2": float(mat.E), "nu12": float(mat.nu),
            "G12": G, "G13": G, "G23": G}


def q_matrix(c: Dict[str, float], angle: float = 0.0) -> np.ndarray:
    """Plane-stress Q (3x3) in the element axes; angle 90 swaps 1 <-> 2."""
    E1, E2, n12, G12 = c["E1"], c["E2"], c["nu12"], c["G12"]
    n21 = n12 * E2 / E1
    den = 1.0 - n12 * n21
    q11, q22, q12 = E1 / den, E2 / den, n12 * E2 / den
    if _is90(angle):
        q11, q22 = q22, q11
    return np.array([[q11, q12, 0.0], [q12, q22, 0.0], [0.0, 0.0, G12]])


def shear_moduli(c: Dict[str, float], angle: float = 0.0
                 ) -> Tuple[float, float]:
    """(G13, G23) in the element axes (90-degree layer swaps them)."""
    return (c["G23"], c["G13"]) if _is90(angle) else (c["G13"], c["G23"])


def _is90(angle) -> bool:
    return abs(float(angle or 0.0) - 90.0) < 1e-9


def validate_material(mat) -> None:
    """ValueError on a malformed orthotropic material (no-op otherwise)."""
    o = getattr(mat, "ortho", None)
    if not is_ortho(mat):
        if o is not None:
            raise ValueError(f"Material {mat.name!r}: 'ortho' constants need "
                             "symmetry 'orthotropic'")
        return
    if not isinstance(o, dict):
        raise ValueError(f"Material {mat.name!r}: symmetry 'orthotropic' "
                         "needs ortho = {E2, G12, G13, G23} (kPa)")
    unknown = set(o) - set(ORTHO_KEYS)
    if unknown:
        raise ValueError(f"Material {mat.name!r}: unknown ortho key(s) "
                         f"{sorted(unknown)} (allowed: {list(ORTHO_KEYS)})")
    for k in ORTHO_KEYS:
        v = o.get(k)
        if not (_finite(v) and v > 0.0):
            raise ValueError(f"Material {mat.name!r}: ortho {k} must be a "
                             f"finite value > 0 kPa (got {v!r})")
    n12 = mat.nu
    if not (_finite(n12) and n12 >= 0.0):
        raise ValueError(f"Material {mat.name!r}: nu12 (nu) must be >= 0 "
                         f"(got {n12!r})")
    if n12 * n12 * o["E2"] / mat.E >= 1.0:
        raise ValueError(f"Material {mat.name!r}: nu12^2 E2 / E1 must be < 1 "
                         "(positive-definite plane-stress stiffness)")


def normalize_ortho(o) -> Optional[dict]:
    if o is None:
        return None
    return {k: float(o[k]) for k in ORTHO_KEYS if k in o}


def preset_material(name: str, key: str = "C24"):
    """A :class:`Material` from a timber preset (orthotropic)."""
    from skyframe.core.model import Material, G_ACCEL
    p = TIMBER_PRESETS[key]
    return Material(name=name, E=p["E1"], nu=p["nu12"],
                    unit_weight=p["unit_weight"], material_type="other",
                    symmetry="orthotropic",
                    mass_density=p["unit_weight"] / G_ACCEL,
                    notes=p["notes"],
                    ortho={"E2": p["E2"], "G12": p["G12"], "G13": p["G13"],
                           "G23": p["G23"]})


def clt_preset(layup: str, material: str) -> dict:
    """``ShellSection.laminate`` of a CLT lay-up (alternating 0 / 90)."""
    _, ts = CLT_LAYUPS[layup]
    return {"layers": [{"t": float(t), "material": material,
                        "angle": 0.0 if i % 2 == 0 else 90.0}
                       for i, t in enumerate(ts)]}


# --------------------------------------------------------------------------- #
# laminate
# --------------------------------------------------------------------------- #
def validate_laminate(ssec, materials) -> None:
    lam = getattr(ssec, "laminate", None)
    if lam is None:
        return
    nm = ssec.name
    if not isinstance(lam, dict) or set(lam) != {"layers"} \
            or not isinstance(lam["layers"], list) or not lam["layers"]:
        raise ValueError(f"Shell section {nm}: laminate must be "
                         "{'layers': [{t, material, angle}, ...]}")
    for i, la in enumerate(lam["layers"]):
        if not isinstance(la, dict) or set(la) - {"t", "material", "angle"}:
            raise ValueError(f"Shell section {nm}: laminate layer {i} keys "
                             "are t, material, angle")
        if not (_finite(la.get("t")) and la["t"] > 0.0):
            raise ValueError(f"Shell section {nm}: laminate layer {i} t must "
                             "be > 0 m")
        if la.get("material") not in materials:
            raise ValueError(f"Shell section {nm}: laminate layer {i} "
                             f"material {la.get('material')!r} is not "
                             "defined")
        ang = la.get("angle", 0.0)
        # Wave C (core.aniso): any finite layer angle
        if not _finite(ang):
            raise ValueError(f"Shell section {nm}: laminate layer {i} angle "
                             "must be a finite angle (deg)")
    if getattr(ssec, "layered", None):
        raise ValueError(f"Shell section {nm}: laminate and layered are "
                         "exclusive")
    from skyframe.core.aniso import laminate_abd    # Wave C: any angle
    A, B, _, _ = laminate_abd(lam, materials)
    scale = max(abs(B).max(), 1e-30)
    h = sum(float(la["t"]) for la in lam["layers"])
    if scale > 1e-9 * abs(A).max() * h:
        raise ValueError(f"Shell section {nm}: the laminate is not symmetric "
                         "about its mid-surface (membrane-bending coupling "
                         "B != 0 is not supported)")


def laminate_resultants(lam: dict, materials
                        ) -> Tuple[np.ndarray, np.ndarray, np.ndarray,
                                   Tuple[float, float]]:
    """(A, B, D, (S13, S23)) per unit width of a laminate (bottom -> top)."""
    layers = lam["layers"]
    h = sum(float(la["t"]) for la in layers)
    z = -h / 2.0
    A = np.zeros((3, 3))
    B = np.zeros((3, 3))
    D = np.zeros((3, 3))
    s13 = s23 = 0.0
    for la in layers:
        t = float(la["t"])
        ang = la.get("angle", 0.0)
        c = constants(materials[la["material"]])
        Q = q_matrix(c, ang)
        zb = z + t / 2.0
        A += t * Q
        B += t * zb * Q
        D += (t ** 3 / 12.0 + t * zb * zb) * Q
        g13, g23 = shear_moduli(c, ang)
        s13 += t * g13
        s23 += t * g23
        z += t
    return A, B, D, (5.0 / 6.0 * s13, 5.0 / 6.0 * s23)


def laminate_weight_thickness(lam: dict, materials, ref_mat) -> float:
    """Thickness of the reference material that weighs as the laminate."""
    w = sum(float(la["t"]) * materials[la["material"]].unit_weight
            for la in lam["layers"])
    return w / ref_mat.unit_weight if ref_mat.unit_weight > 0 else \
        sum(float(la["t"]) for la in lam["layers"])


def homogeneous_resultants(mat, h: float):
    """(A, D, S13, S23) of a homogeneous (orthotropic) plate."""
    c = constants(mat)
    Q = q_matrix(c)
    return (h * Q, h ** 3 / 12.0 * Q, 5.0 / 6.0 * c["G13"] * h,
            5.0 / 6.0 * c["G23"] * h)


def plate_deflection_navier(a: float, b: float, q: float, D11: float,
                            D22: float, H: float, terms: int = 99) -> float:
    """Centre deflection of a simply supported orthotropic rectangle a x b
    under uniform q (Lekhnitskii / Timoshenko & Woinowsky-Krieger eq.
    (b) of section 87): ``w = 16 q / pi^6 sum_{m,n odd} sin(m pi/2)
    sin(n pi/2) / (m n (D11 (m/a)^4 + 2 H (m n / a b)^2 + D22 (n/b)^4))``
    with ``H = D12 + 2 D66``."""
    w = 0.0
    for m in range(1, terms + 1, 2):
        for n in range(1, terms + 1, 2):
            den = m * n * (D11 * (m / a) ** 4 + 2.0 * H * (m * n / (a * b)) ** 2
                           + D22 * (n / b) ** 4)
            w += math.sin(m * math.pi / 2) * math.sin(n * math.pi / 2) / den
    return 16.0 * q / math.pi ** 6 * w
