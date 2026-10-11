"""Rayleigh damping on springs, links and hinges (OpenSees runtime side).

See :mod:`skyframe.core.damping_options` for the option, the per-kind
participation table and the rationale.  Mechanics:

* ``begin(eng, th)`` / ``end(eng)`` bracket the time-history ``_build``;
  while active, :func:`flag` returns the element's ``-doRayleigh``
  argument for every participating zeroLength / twoNodeLink and records
  the element (``asm.rl_elements``).  Outside a TH build — and always with
  ``rayleigh_on_links == "none"`` — it returns ``[]``, so every
  ``ops.element`` call is the exact legacy call (bit-identical).
* ``apply(eng, asm, a1)`` runs right after the case's ``ops.rayleigh``:
  the participating elements get their own factors through an
  ``ops.region(..., '-eleOnly', ..., '-rayleigh', 0, betaK, betaK0,
  betaKc)`` with ``a1`` in the slot of ``rayleigh_stiffness_basis``
  (the global call would otherwise give them the case's basis).  The
  region carries no nodes, so the nodal mass-proportional ``a0`` term is
  untouched (the elements are massless).
* :func:`tracker_elements` gives the energy tracker
  (:mod:`skyframe.engine.thoptions`) each COMMITTED-basis element's
  basic-deformation matrix ``A`` (``d = A [u_i; u_j]``, built from the
  element's own ``xaxis/yaxis/zaxis`` responses) so it can book the
  damping force the integrator ACTUALLY applied: OpenSees refreshes an
  element's committed stiffness ``Kc`` inside ``commitState``, so a
  ``reactions('-dynamic')`` read after the step uses ``Kc(n+1)`` while
  equilibrium at ``t_n+1`` was solved with ``Kc(n)``.  The difference
  ``a1 A^T diag(k_n - k_n+1) A v`` is non-zero only on steps where a
  material tangent changes (yield / unload / gap events) — exactly where
  the uncorrected balance broke.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import openseespy.opensees as ops

from skyframe.core import damping_options as _dmpo

#: element kinds damped under "elastic_only" (linear-elastic springs)
ELASTIC_KINDS = frozenset({"spring", "named_spring", "elastic_link",
                           "panel_zone"})
#: additional kinds damped under "all" (nonlinear springs / hinges / links)
NONLINEAR_KINDS = frozenset({"hinge", "user_hinge", "named_spring_nl",
                             "compression_only_spring", "isolator_link",
                             "multilinear_link", "hysteretic_link",
                             "gap_link", "hook_link"})
#: kinds that never take Rayleigh (documented in core.damping_options)
NEVER_KINDS = frozenset({"damper_link", "bearing", "hinge_tie", "dummy"})

_BASIS_SLOT = {"current": 1, "initial": 2, "committed": 3}
REGION_TAG = 91001


class _Ctx:
    def __init__(self, mode: str, basis: str) -> None:
        self.mode = mode
        self.basis = basis


def begin(eng, th) -> None:
    """Activate the option for the TH build of case ``th``."""
    o = _dmpo.effective(eng.model)
    eng._rl_ctx = (None if o["rayleigh_on_links"] == "none"
                   else _Ctx(o["rayleigh_on_links"],
                             o["rayleigh_stiffness_basis"]))


def end(eng) -> None:
    eng._rl_ctx = None


def participates(mode: str, kind: str) -> bool:
    if kind in ELASTIC_KINDS:
        return mode in ("elastic_only", "all")
    if kind in NONLINEAR_KINDS:
        return mode == "all"
    return False


def flag(eng, asm, etag: int, kind: str, dirs, element: str = "zeroLength"
         ) -> List:
    """``-doRayleigh`` arguments for element ``etag`` (``[]`` = legacy)."""
    ctx = getattr(eng, "_rl_ctx", None)
    if ctx is None or not participates(ctx.mode, kind):
        return []
    lst = getattr(asm, "rl_elements", None)
    if lst is None:
        lst = []
        asm.rl_elements = lst
    lst.append({"ele": int(etag), "kind": kind, "type": element,
                "dirs": [int(d) for d in dirs]})
    return ["-doRayleigh", 1] if element == "zeroLength" else ["-doRayleigh"]


def apply(eng, asm, a1: float) -> Optional[dict]:
    """Give the participating elements ``a1`` on the chosen basis.

    Returns the summary stored in ``THResults.extra["link_rayleigh"]``
    (``None`` when the option is off / nothing participates)."""
    ctx = getattr(eng, "_rl_ctx", None)
    lst = getattr(asm, "rl_elements", None) or []
    if ctx is None:
        return None
    asm.rl_a1 = float(a1)
    asm.rl_basis = ctx.basis
    if lst and a1 != 0.0:
        f = [0.0, 0.0, 0.0, 0.0]
        f[_BASIS_SLOT[ctx.basis]] = float(a1)
        ops.region(REGION_TAG, "-eleOnly", *[e["ele"] for e in lst],
                   "-rayleigh", *f)
    counts: Dict[str, int] = {}
    for e in lst:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {"rayleigh_on_links": ctx.mode,
            "rayleigh_stiffness_basis": ctx.basis,
            "a1": float(a1), "elements": dict(sorted(counts.items()))}


def basic_matrix(e: dict) -> np.ndarray:
    """``A`` (n_dirs x 12): basic deformations of element ``e`` from the
    nodal displacements ``[u_i, theta_i, u_j, theta_j]`` (global)."""
    etag = e["ele"]
    R = np.array([ops.eleResponse(etag, k)
                  for k in ("xaxis", "yaxis", "zaxis")], float)
    rows = []
    if e["type"] == "zeroLength":
        for d in e["dirs"]:
            r = np.zeros(12)
            if d <= 3:
                r[0:3], r[6:9] = -R[d - 1], R[d - 1]
            else:
                r[3:6], r[9:12] = -R[d - 4], R[d - 4]
            rows.append(r)
        return np.array(rows)
    # twoNodeLink (shearDistI = 0.5, no P-Delta moments)
    ni, nj = ops.eleNodes(etag)
    L = float(np.linalg.norm(np.array(ops.nodeCoord(nj), float)
                             - np.array(ops.nodeCoord(ni), float)))
    T = np.zeros((12, 12))
    for b in range(4):
        T[3 * b:3 * b + 3, 3 * b:3 * b + 3] = R
    s = 0.5 * L
    loc = {1: {0: -1.0, 6: 1.0},
           2: {1: -1.0, 5: -s, 7: 1.0, 11: -s},
           3: {2: -1.0, 4: s, 8: 1.0, 10: s},
           4: {3: -1.0, 9: 1.0},
           5: {4: -1.0, 10: 1.0},
           6: {5: -1.0, 11: 1.0}}
    for d in e["dirs"]:
        r = np.zeros(12)
        for i, c in loc[d].items():
            r[i] = c
        rows.append(r @ T)
    return np.array(rows)


def material_tangents(etag: int, n: int) -> np.ndarray:
    """Current tangent of each of the element's ``n`` basic materials."""
    k = np.zeros(n)
    for i in range(n):
        r = ops.eleResponse(etag, "material", str(i + 1), "tangent")
        k[i] = float(r[0]) if r else 0.0
    return k


def tracker_elements(asm) -> List[dict]:
    """Committed-basis participating elements for the energy tracker:
    ``{"ele", "nodes", "A"}`` (empty unless the option is on, a1 != 0 and
    the basis is ``committed``)."""
    lst = getattr(asm, "rl_elements", None) or []
    if (not lst or getattr(asm, "rl_basis", None) != "committed"
            or not getattr(asm, "rl_a1", 0.0)):
        return []
    out = []
    for e in lst:
        out.append({"ele": e["ele"],
                    "nodes": tuple(int(t) for t in ops.eleNodes(e["ele"])),
                    "A": basic_matrix(e)})
    return out
