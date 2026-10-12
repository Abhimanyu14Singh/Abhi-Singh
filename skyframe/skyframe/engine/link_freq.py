"""Frequency-dependent link properties in Steady-State / PSD solves.

Definition: :mod:`skyframe.core.link_freq`.  Hooked into
:mod:`skyframe.engine.frequency` with two lines (``attach`` at the end of
the basis build, ``response_block`` in place of the uncoupled block when
the model assigns any property).

Method (exact in the retained modal subspace).  The elastic basis is the
standard build (each property's link with its ELASTIC value: elastic
``stiffness[d]``, damper none).  Each assigned (link, DOF) is a row
``b_r`` of modal deformations (``phi_j - phi_i`` on the global DOF, or
``e . (phi_j - phi_i)`` along the axis for AXIAL) and a complex
impedance change

    D_r(w) = (K_r(f) - K0_r) + i w C_r(f).

Per frequency the coupled modal equations are solved densely:

    (diag(1/H_i(w)) + B^T D(w) B) q = P a(f),    Z = q Phi + residual

(``H_i`` the case's modal receptances — viscous or hysteretic — and
``residual`` the unchanged static residual term of method "direct").
The extra link forces ``dF_r = D_r b_r . q`` are added to the support
reactions of grounded link ends (``-dF`` at end i, ``+dF`` at end j,
along the DOF / axis) and to the base totals.  For a model whose
assigned link DOFs are spanned by the retained modes (e.g. method
"direct" with mass at the link nodes) this is the exact complex direct
solution ``(K + dK(w) - w^2 M + i w C + i w dC(w)) u = F``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

from skyframe.core import link_freq as _lf

_DOF = {lab: k for k, lab in enumerate(_lf.GLOBAL_DOFS)}


class _LFD:
    rows: np.ndarray          # (nr, nm)
    k0: np.ndarray            # (nr,)
    tables: List[list]
    G: np.ndarray             # (nr, Q) reaction/base field per unit dF
    labels: List[str]


def attach(eng, b, case) -> None:
    """Set ``b.lfd`` when the model assigns frequency-dependent props."""
    model = eng.model
    asg = _lf.assignments(model)
    if not asg:
        b.lfd = None
        return
    lay = b.layout
    asm = lay.asm
    S, idx = b.S, b.node_index
    nm = S.shape[0]
    links = {lk.uid: lk for lk in model.links}
    sup_idx = {t: k for k, t in enumerate(lay.support_tags)}
    rows, k0s, tabs, Gs, labs = [], [], [], [], []
    for name, uid, lab, tab in asg:
        lk = links[uid]

        def tag(p):
            try:
                return eng._find_node(asm, tuple(p))
            except ValueError:
                return None
        ti, tj = tag(lk.pi), tag(lk.pj)
        if lab == _lf.AXIAL:
            e = np.asarray(lk.pj, float) - np.asarray(lk.pi, float)
            e = e / float(np.linalg.norm(e))
            vec = np.concatenate([e, np.zeros(3)])
            k0 = 0.0                                # damper: no stiffness
        else:
            vec = np.zeros(6)
            vec[_DOF[lab]] = 1.0
            k0 = (float(lk.stiffness[_DOF[lab]])
                  if getattr(lk, "link_type", "elastic") == "elastic"
                  else 0.0)
        row = np.zeros(nm)
        for t, sgn in ((tj, 1.0), (ti, -1.0)):
            if t is not None and t in idx:
                row += sgn * (S[:, 6 * idx[t]:6 * idx[t] + 6] @ vec)
        g = np.zeros(lay.size)
        dre: Dict[int, List[float]] = {}
        for t, sgn in ((ti, -1.0), (tj, 1.0)):
            if t is not None and t in sup_idx:
                o = lay.o_react + 6 * sup_idx[t]
                g[o:o + 6] += sgn * vec
                acc = dre.setdefault(t, [0.0] * 6)
                for d in range(6):
                    acc[d] += sgn * vec[d]
        if dre:
            base = eng._base_totals(asm, dre)
            for k, key in enumerate(("FX", "FY", "FZ", "MX", "MY", "MZ")):
                g[lay.o_base + k] += base[key]
        rows.append(row)
        k0s.append(k0)
        tabs.append(tab)
        Gs.append(g)
        labs.append(f"{name}:{uid}:{lab}")
    x = _LFD()
    x.rows = np.array(rows)
    x.k0 = np.array(k0s)
    x.tables = tabs
    x.G = np.array(Gs)
    x.labels = labs
    b.lfd = x
    b.warnings.append("frequency-dependent link properties applied: "
                      + ", ".join(labs))


def response_block(b, case, A: np.ndarray, f: np.ndarray,
                   cols: Optional[np.ndarray] = None) -> np.ndarray:
    """Coupled complex response ``Z[k, q]`` for one frequency block."""
    from skyframe.engine.frequency import modal_frf
    x = b.lfd
    w = 2.0 * math.pi * np.asarray(f, dtype=float)
    H = modal_frf(b.omegas, w, case.damping, case.damping_type)
    Phi = b.Phi if cols is None else b.Phi[:, cols]
    G = x.G if cols is None else x.G[:, cols]
    nr = x.rows.shape[0]
    K = np.zeros((f.size, nr))
    C = np.zeros((f.size, nr))
    for r, tab in enumerate(x.tables):
        K[:, r], C[:, r] = _lf.interp(tab, f)
    D = (K - x.k0[None, :]) + 1j * w[:, None] * C          # (nf, nr)
    PA = A @ b.P.T                                          # (nf, nm)
    B = x.rows
    Q = np.zeros((f.size, b.P.shape[0]), dtype=complex)
    for k in range(f.size):
        M = np.diag(1.0 / H[k]) + (B.T * D[k]) @ B
        Q[k] = np.linalg.solve(M, PA[k])
    Z = Q @ Phi
    dF = D * (Q @ B.T)                                      # (nf, nr)
    Z = Z + dF @ G
    if b.R is not None:
        R = b.R if cols is None else b.R[:, cols]
        hres = (1.0 / (1.0 + 2.0j * case.damping)
                if case.damping_type == "hysteretic" else 1.0)
        Z = Z + (A * hres) @ R
    return Z


__all__ = ["attach", "response_block"]
