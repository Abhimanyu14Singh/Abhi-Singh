"""Creep and shrinkage of SHELLS in step-by-step staged construction
(``StagedCase.time_dependent.shell_creep: true``; method ``"step"``).

CONTRACT "Eigen shift / cutoff / tolerance, ... shell creep / shrinkage".

The frame algorithm of :mod:`skyframe.engine.staged_creep` (Bazant
step-by-step superposition with the age-adjusted effective modulus) is
applied to every shell element whose material has a time-dependent law,
with plate kinematics instead of beam kinematics:

* per solve ``j`` (operations at ``T_s``, creep steps at their mid time)
  the element-average MECHANICAL membrane strain ``e_j`` and curvature
  ``k_j`` (region local axes ``e1 / e2``; engineering shear / twist) are
  recovered exactly from the nodal displacements / rotations by the
  boundary integral ``int grad u dA = sum_edges (u_a + u_b)/2 (x) n ds``
  (``w`` slopes ``g = (-theta.e2, theta.e1)``), minus the strain imposed in
  that solve; ``E_j e_j = D^-1 d sigma_j`` (``D`` = plane stress with
  ``E = 1``) is stored with ``tau_j``;
* creep step ``t_{i-1} -> t_i``: the imposed (stress-free) strain

      d_eps0 = sum_j E_j e_j [J(t_i, tau_j) - J(t_{i-1}, tau_j)]
               - [eps_sh(t_i) - eps_sh(t_{i-1})] (1, 1, 0)
      d_kap0 = sum_j E_j k_j [J(t_i, tau_j) - J(t_{i-1}, tau_j)]

  (ages from the shell's casting time) and the shell modulus
  ``1/J(t_i, tau_mid)`` (instantaneous solves: ``1/J(T_s, T_s)``) — the
  same compliance as the frames;
* the imposed strains enter as the exact work-equivalent nodal loads of a
  constant strain field (the patch-test boundary form used by the shell
  temperature loads): every element edge ``a -> b`` adds
  ``(L/2) N0 n`` (membrane, ``N0 = D_m eps0``) and the moment
  ``m = h2 e1 - h1 e2`` with ``h = (L/2) M0 n`` (``M0 = D_b kap0``) at
  ``a`` and ``b`` — a free plate then strains / curves exactly by
  ``eps0`` / ``kap0`` with zero stress.

Uniform shrinkage is applied per REGION (also to shells that never
carried load); creep strains per element.  A homogeneous plate under a
constant load therefore creeps exactly as ``sigma J(t, t0)``.  Layered /
orthotropic sections use the total thickness and the mean modifiers
(:func:`skyframe.engine.thermal_ext._shell_props`).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import timedep as _tdm

KEY = "__shells__"


def enabled(td) -> bool:
    return isinstance(td, dict) and bool(td.get("shell_creep"))


def _ckey(xyz) -> Tuple[float, float, float]:
    c = np.mean(np.asarray(xyz, dtype=float), axis=0)
    return tuple(round(float(v), 6) for v in c)


def _frame(region):
    from skyframe.core.loads_ext import shell_local_axes
    e1, e2, e3 = (np.asarray(v, dtype=float)
                  for v in shell_local_axes(region))
    return e1, e2, e3


def _poly(xyz, e1, e2):
    """2-D local coordinates and the signed area of an element."""
    P = np.array([[float(p @ e1), float(p @ e2)] for p in xyz])
    x, y = P[:, 0], P[:, 1]
    A = 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    return P, A


def _grad(P, vals, A):
    """Average gradient (2x2) of a field linear on the edges:
    ``(1/A) sum (v_a + v_b)/2 (x) (dy, -dx)`` (signed area)."""
    G = np.zeros((2, 2))
    n = len(P)
    for k in range(n):
        a, b = k, (k + 1) % n
        dx, dy = P[b] - P[a]
        G += np.outer(0.5 * (vals[a] + vals[b]), [dy, -dx])
    return G / A


def element_strains(xyz, disp, e1, e2) -> Tuple[np.ndarray, np.ndarray]:
    """(membrane [ex, ey, gxy], curvature [kx, ky, kxy]) element averages
    from nodal 6-dof displacements (region local axes)."""
    P, A = _poly(xyz, e1, e2)
    U = np.array([[float(np.asarray(d[:3]) @ e1),
                   float(np.asarray(d[:3]) @ e2)] for d in disp])
    Gm = _grad(P, U, A)
    Gs = np.array([[-float(np.asarray(d[3:6]) @ e2),
                    float(np.asarray(d[3:6]) @ e1)] for d in disp])
    Gb = _grad(P, Gs, A)
    return (np.array([Gm[0, 0], Gm[1, 1], Gm[0, 1] + Gm[1, 0]]),
            np.array([Gb[0, 0], Gb[1, 1], Gb[0, 1] + Gb[1, 0]]))


def _dmat(E: float, nu: float, t: float, bend: bool) -> np.ndarray:
    f = E * (t ** 3 / 12.0 if bend else t) / (1.0 - nu * nu)
    return f * np.array([[1.0, nu, 0.0], [nu, 1.0, 0.0],
                         [0.0, 0.0, 0.5 * (1.0 - nu)]])


def nodal_loads(xyz, e1, e2, N0: np.ndarray, M0: np.ndarray):
    """Work-equivalent nodal (force, moment) global vectors of constant
    resultants ``N0`` / ``M0`` = [xx, yy, xy] over one element."""
    P, A = _poly(xyz, e1, e2)
    s = 1.0 if A >= 0.0 else -1.0
    Nt = np.array([[N0[0], N0[2]], [N0[2], N0[1]]])
    Mt = np.array([[M0[0], M0[2]], [M0[2], M0[1]]])
    n = len(P)
    out = [np.zeros(6) for _ in range(n)]
    for k in range(n):
        a, b = k, (k + 1) % n
        dx, dy = P[b] - P[a]
        nv = s * np.array([dy, -dx])
        f2 = 0.5 * Nt @ nv
        h = 0.5 * Mt @ nv
        F = f2[0] * e1 + f2[1] * e2
        Mv = h[1] * e1 - h[0] * e2
        for i in (a, b):
            out[i][:3] += F
            out[i][3:] += Mv
    return out


class ShellCreep:
    """Per-run shell creep state (history + imposed-strain builder)."""

    def __init__(self, model, shell_law: Dict[str, tuple]):
        self.model = model
        self.law = shell_law                  # uid -> (props, E28, fc)
        self.regions = {r.uid: r for r in model.shells}
        self.hist: Dict[tuple, List[tuple]] = {}
        self._frames: Dict[str, tuple] = {}

    def frame(self, rid: str):
        if rid not in self._frames:
            self._frames[rid] = _frame(self.regions[rid])
        return self._frames[rid]

    def J(self, rid: str, t_age: float, tau_age: float) -> float:
        p, E28, fc = self.law[rid]
        return _tdm.law_compliance(p, E28, t_age, tau_age, fc)

    def eps_sh(self, rid: str, age: float) -> float:
        if age <= 0.0:
            return 0.0
        p, _E, fc = self.law[rid]
        return _tdm.law_shrinkage(p, age, fc)

    # ---------------------------------------------------------- scales
    def inst_scales(self, sc_: dict, active, t_cast, tnow: float) -> None:
        for rid in active["shells"]:
            if rid in self.law:
                age = max(tnow - t_cast[("shells", rid)], 1e-6)
                sc_[("shells", rid)] = (1.0 / self.J(rid, age, age)
                                        / self.law[rid][1])

    def creep_scales(self, scales: dict, active, t_cast, tb: float,
                     tm: float) -> None:
        for rid in active["shells"]:
            if rid in self.law:
                tc = t_cast[("shells", rid)]
                scales[("shells", rid)] = (
                    1.0 / self.J(rid, max(tb - tc, 1e-6),
                                 max(tm - tc, 1e-6)) / self.law[rid][1])

    # --------------------------------------------------------- history
    def record(self, sub, asm, res, tau: float,
               imposed: Optional[dict]) -> None:
        regions = {r.uid: r for r in sub.shells}
        for sq in asm.shell_quads:
            rid = sq["region"]
            if rid not in self.law or rid not in regions:
                continue
            reg = regions[rid]
            ssec = sub.shell_sections[reg.section]
            E = sub.materials[ssec.material].E * float(
                getattr(ssec, "mod", 1.0))
            tags = list(sq["nodes"])
            xyz = [np.asarray(asm.node_coords[t], dtype=float) for t in tags]
            e1, e2, _e3 = self.frame(rid)
            em, eb = element_strains(xyz, [res.node_disp[t] for t in tags],
                                     e1, e2)
            ck = _ckey(xyz)
            if imposed:
                i_m, i_b = self.imposed_at(imposed, rid, ck)
                em, eb = em - i_m, eb - i_b
            self.hist.setdefault((rid, ck), []).append(
                (float(tau), E * em, E * eb))

    @staticmethod
    def imposed_at(imposed: dict, rid: str, ck) -> Tuple[np.ndarray,
                                                         np.ndarray]:
        m = np.zeros(3)
        b = np.zeros(3)
        r = (imposed.get("regions") or {}).get(rid)
        if r is not None:
            m = m + r[0]
            b = b + r[1]
        e = (imposed.get("elements") or {}).get((rid, ck))
        if e is not None:
            m = m + e[0]
            b = b + e[1]
        return m, b

    def imposed(self, active, t_cast, ta: float, tb: float) -> dict:
        """The step's imposed strains ``{"regions": {rid: (m, b)},
        "elements": {(rid, ck): (m, b)}}`` (empty dict: none)."""
        regs, eles = {}, {}
        for rid in active["shells"]:
            if rid not in self.law:
                continue
            tc = t_cast[("shells", rid)]
            dsh = self.eps_sh(rid, tb - tc) - self.eps_sh(rid, ta - tc)
            if dsh:
                regs[rid] = (np.array([-dsh, -dsh, 0.0]), np.zeros(3))
        for (rid, ck), hist in self.hist.items():
            if rid not in active["shells"] or rid not in self.law:
                continue
            tc = t_cast[("shells", rid)]
            m = np.zeros(3)
            b = np.zeros(3)
            for tau, sm, sb in hist:
                dJ = (self.J(rid, tb - tc, tau - tc)
                      - self.J(rid, ta - tc, tau - tc))
                if dJ:
                    m += sm * dJ
                    b += sb * dJ
            if np.any(m) or np.any(b):
                eles[(rid, ck)] = (m, b)
        if not regs and not eles:
            return {}
        return {"regions": regs, "elements": eles}


def apply(eng, asm, imposed: dict, scale: float) -> None:
    """``staged_creep.apply_imposed`` hook: nodal loads of the imposed
    shell strains on the live domain (inside the open load pattern)."""
    from skyframe.engine import thermal_ext as _thx
    regions = {r.uid: r for r in eng.model.shells}
    frames: Dict[str, tuple] = {}
    nodal: Dict[int, np.ndarray] = {}
    for qi, sq in enumerate(asm.shell_quads):
        rid = sq["region"]
        if rid not in regions:
            continue
        tags = list(sq["nodes"])
        xyz = [np.asarray(asm.node_coords[t], dtype=float) for t in tags]
        m, b = ShellCreep.imposed_at(imposed, rid, _ckey(xyz))
        if not (np.any(m) or np.any(b)):
            continue
        if rid not in frames:
            frames[rid] = _frame(regions[rid])
        e1, e2, _e3 = frames[rid]
        Em, Eb, nu, t, _a = _thx._shell_props(eng, regions[rid], qi)
        N0 = _dmat(Em, nu, t, False) @ (m * scale)
        M0 = _dmat(Eb, nu, t, True) @ (b * scale)
        for tg, v in zip(tags, nodal_loads(xyz, e1, e2, N0, M0)):
            nodal[tg] = nodal.get(tg, np.zeros(6)) + v
    for tg in sorted(nodal):
        ops.load(tg, *[float(x) for x in nodal[tg]])


__all__ = ["KEY", "enabled", "element_strains", "nodal_loads",
           "ShellCreep", "apply"]
