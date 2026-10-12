"""Buckling case options (v1.24) — model side.

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".
The solver is :mod:`skyframe.engine.buckling_fem`; the member shell-FEM
tool (BucklingFEM) is :mod:`skyframe.core.member_buckling`.
"""

from __future__ import annotations

FORMULATIONS = ("frame", "fem")


def validate_case(bc) -> None:
    """Raise ``ValueError`` on malformed v1.24 buckling-case options."""
    f = getattr(bc, "formulation", "frame")
    if f not in FORMULATIONS:
        raise ValueError(f"Buckling case {bc.name}: formulation must be one "
                         f"of {'|'.join(FORMULATIONS)} (got {f!r})")
    for k in ("include_shells", "include_links"):
        v = getattr(bc, k, True)
        if not isinstance(v, bool):
            raise ValueError(f"Buckling case {bc.name}: {k} must be a "
                             f"boolean (got {v!r})")
