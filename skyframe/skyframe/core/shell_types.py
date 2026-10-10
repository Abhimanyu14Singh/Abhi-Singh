"""ETABS shell element types and one-way slab options (solver-agnostic).

CONTRACT "Shell element types, one-way slabs and shell benchmarks".

``ShellSection.shell_type`` picks the OpenSees element family used for every
meshed element of that section and whether membrane and/or bending
stiffness is carried:

==============  ======================  ======================  ==========
shell_type      quad element            triangle element        carries
==============  ======================  ======================  ==========
``shell``       ShellMITC4              ShellDKGT               both (legacy default, byte-identical)
``shell_thin``  ShellDKGQ (Kirchhoff)   ShellDKGT (Kirchhoff)   both
``shell_thick`` ASDShellQ4 (Mindlin)    ASDShellT3 (Mindlin)    both
``membrane``    ASDShellQ4              ASDShellT3              membrane only
``plate_thin``  ShellDKGQ               ShellDKGT               bending only
``plate_thick`` ASDShellQ4              ASDShellT3              bending only
==============  ======================  ======================  ==========

"Membrane only" / "bending only" are realised with the existing exact
shell-modifier machinery (:mod:`skyframe.engine.shell_modifiers`): the
dropped stiffness group is multiplied by ``RESIDUAL`` (1e-6, never exactly
zero, so the out-of-plane / in-plane DOFs of nodes not framed into anything
stay non-singular — the same role as ETABS' automatic restraint of
unstiffened DOFs).  ``membrane`` multiplies the bending AND transverse-shear
factors (m11, m22, m12, v13, v23) by RESIDUAL; ``plate_*`` multiplies the
membrane factors (f11, f22, f12) by RESIDUAL.  User modifiers multiply on
top.  The drilling stiffness of ASDShellQ4/T3 is a membrane quantity, so a
``membrane`` element keeps a full, well-posed drilling DOF (the frame-shell
junction is never singular).

One-way slabs (``ShellRegion.distribution``): membrane-behavior slabs only.
``"two_way"`` (default) is the existing 45-degree tributary rule;
``"one_way"`` with ``one_way_dir`` 1 or 2 spans the slab along region local
axis 1 (edge c0 -> c1) or 2 (in-plane normal to 1, n x e1) and sends the
whole load to the two SUPPORT edges that the span direction crosses (see
:mod:`skyframe.core.oneway`).
"""

from __future__ import annotations

from typing import Dict, Tuple

SHELL_TYPES = ("shell", "shell_thin", "shell_thick", "membrane",
               "plate_thin", "plate_thick")
DEFAULT_SHELL_TYPE = "shell"

# Recommendation for NEW models (benchmark-driven, see CONTRACT): exposed
# only — existing models and the dataclass default stay on "shell".
RECOMMENDED_SHELL_TYPE = "shell_thick"

RESIDUAL = 1e-6        # stiffness factor of a dropped (membrane|bending) group

# shell_type -> (quad element, triangle element)
ELEMENTS: Dict[str, Tuple[str, str]] = {
    "shell": ("ShellMITC4", "ShellDKGT"),
    "shell_thin": ("ShellDKGQ", "ShellDKGT"),
    "shell_thick": ("ASDShellQ4", "ASDShellT3"),
    "membrane": ("ASDShellQ4", "ASDShellT3"),
    "plate_thin": ("ShellDKGQ", "ShellDKGT"),
    "plate_thick": ("ASDShellQ4", "ASDShellT3"),
}

MEMBRANE_KEYS = ("f11", "f22", "f12")
BENDING_KEYS = ("m11", "m22", "m12", "v13", "v23")

DISTRIBUTIONS = ("two_way", "one_way")


def shell_type_of(ssec) -> str:
    return getattr(ssec, "shell_type", DEFAULT_SHELL_TYPE) or \
        DEFAULT_SHELL_TYPE


def element_names(shell_type: str) -> Tuple[str, str]:
    return ELEMENTS[shell_type]


def type_factors(shell_type: str) -> Dict[str, float]:
    """Stiffness factors the type multiplies onto the section modifiers."""
    if shell_type == "membrane":
        return {k: RESIDUAL for k in BENDING_KEYS}
    if shell_type in ("plate_thin", "plate_thick"):
        return {k: RESIDUAL for k in MEMBRANE_KEYS}
    return {}


def validate_shell_type(ssec) -> None:
    st = getattr(ssec, "shell_type", DEFAULT_SHELL_TYPE)
    if st not in SHELL_TYPES:
        raise ValueError(f"Shell section {ssec.name}: shell_type must be one "
                         f"of {'|'.join(SHELL_TYPES)} (got {st!r})")
    if getattr(ssec, "layered", None) and st not in (
            "shell", "shell_thin", "shell_thick"):
        raise ValueError(f"Shell section {ssec.name}: a layered section "
                         "needs shell_type shell|shell_thin|shell_thick "
                         f"(got {st!r})")


def validate_region_distribution(region) -> None:
    dist = getattr(region, "distribution", "two_way")
    if dist not in DISTRIBUTIONS:
        raise ValueError(f"Shell {region.uid}: distribution must be "
                         f"two_way|one_way (got {dist!r})")
    d = getattr(region, "one_way_dir", 1)
    if d not in (1, 2):
        raise ValueError(f"Shell {region.uid}: one_way_dir must be 1 or 2 "
                         f"(got {d!r})")
    if dist == "one_way":
        if region.behavior != "membrane" or region.kind != "slab":
            raise ValueError(f"Shell {region.uid}: one_way distribution "
                             "needs a membrane-behavior slab")
        if len(region.corners) != 4 or any(
                getattr(op, "polygon", None) for op in region.openings):
            raise ValueError(f"Shell {region.uid}: one_way distribution "
                             "needs a 4-corner region (no polygon openings)")
        c = [tuple(map(float, p)) for p in region.corners]
        signs = set()
        for k in range(4):
            a0, a1, a2 = c[k], c[(k + 1) % 4], c[(k + 2) % 4]
            u = [a1[i] - a0[i] for i in range(3)]
            v = [a2[i] - a1[i] for i in range(3)]
            x = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
                 u[0] * v[1] - u[1] * v[0])
            signs.add(x)
        ref = next(iter(signs))
        if any(sum(x[i] * ref[i] for i in range(3)) <= 0.0 for x in signs):
            raise ValueError(f"Shell {region.uid}: one_way distribution "
                             "needs a convex region")


# ------------------------------------------------------------- serialization
def section_extra_to_dict(ssec) -> dict:
    st = getattr(ssec, "shell_type", DEFAULT_SHELL_TYPE)
    return {} if st == DEFAULT_SHELL_TYPE else {"shell_type": st}


def section_extra_from_dict(sd: dict) -> dict:
    return ({"shell_type": str(sd["shell_type"])}
            if sd.get("shell_type") else {})


def region_extra_to_dict(r) -> dict:
    out = {}
    if getattr(r, "distribution", "two_way") != "two_way":
        out["distribution"] = r.distribution
    if getattr(r, "one_way_dir", 1) != 1:
        out["one_way_dir"] = r.one_way_dir
    return out


def region_extra_from_dict(rd: dict) -> dict:
    out = {}
    if rd.get("distribution"):
        out["distribution"] = str(rd["distribution"])
    if "one_way_dir" in rd:
        out["one_way_dir"] = int(rd["one_way_dir"])
    return out
