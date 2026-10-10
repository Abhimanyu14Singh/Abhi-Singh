"""OpenSees wiring of ``ShellSection.shell_type`` (element family + the
membrane-only / bending-only stiffness groups).

CONTRACT "Shell element types, one-way slabs and shell benchmarks".  The
default type ``"shell"`` goes through EXACTLY the legacy calls
(:func:`skyframe.engine.shell_modifiers.elastic_shell_section` with the
section itself, ``ShellMITC4`` / ``ShellDKGT``), so default models are
byte-identical.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Sequence, Tuple

import openseespy.opensees as ops

from skyframe.core import shell_types as _st
from skyframe.core.modifiers import SHELL_STIFF_KEYS
from skyframe.engine.shell_modifiers import elastic_shell_section


def _typed_view(ssec):
    """The section with the shell-type factors multiplied onto its
    stiffness modifiers (``ssec`` itself when the type adds none)."""
    extra = _st.type_factors(_st.shell_type_of(ssec))
    if not extra:
        return ssec
    view = SimpleNamespace(
        name=ssec.name, material=ssec.material, thickness=ssec.thickness,
        mod=ssec.mod, layered=getattr(ssec, "layered", None),
        total_thickness=ssec.total_thickness)
    for k in SHELL_STIFF_KEYS:
        setattr(view, k, float(getattr(ssec, k, 1.0)) * extra.get(k, 1.0))
    return view


def build_section(stag: int, ssec, mat, scale: float,
                  nd_tag: int) -> Tuple[int, str]:
    """Linear shell section honouring ``shell_type`` (see module doc)."""
    return elastic_shell_section(stag, _typed_view(ssec), mat, scale, nd_tag)


def element_name(ssec, n_nodes: int) -> str:
    quad, tri = _st.element_names(_st.shell_type_of(ssec))
    return tri if n_nodes == 3 else quad


def add_element(ssec, etag: int, node_tags: Sequence[int], stag: int
                ) -> str:
    """Create one shell element; returns the OpenSees element name.

    ASDShellQ4 gets ``-local`` = the ShellMITC4 local x axis (from the
    1-4 side midpoint to the 2-3 side midpoint) so its 'stresses'
    resultants are reported in the same element system and Gauss order
    as the legacy ShellMITC4 (probed)."""
    name = element_name(ssec, len(node_tags))
    extra: List[float] = []
    if name == "ASDShellQ4":
        p = [ops.nodeCoord(n) for n in node_tags]
        v = [0.5 * (p[1][k] + p[2][k] - p[0][k] - p[3][k]) for k in range(3)]
        nv = sum(c * c for c in v) ** 0.5
        extra = ["-local", *[c / nv for c in v]]
    ops.element(name, etag, *node_tags, stag, *extra)
    return name
