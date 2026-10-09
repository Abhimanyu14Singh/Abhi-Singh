"""ETABS-parity response-spectrum modal combination + load participation.

This module holds the bulk of the v1.13 "modal combination methods + load
participation" feature so the shared engine file only carries two small,
self-contained hooks (see ``OpenSeesEngine.run_response_spectrum`` and
``OpenSeesEngine.run``).

Modal combination (``ResponseSpectrumCase.combo_method``)
---------------------------------------------------------
Per response quantity with signed modal values ``r_i`` (mode i, circular
frequency ``w_i``, cyclic ``f_i = w_i / 2 pi``, constant damping ``z``):

* ``CQC``   ``R = sqrt(sum_ij rho_ij r_i r_j)``, Der Kiureghian
            ``rho_ij = 8 z^2 (1+b) b^1.5 / ((1-b^2)^2 + 4 z^2 b (1+b)^2)``,
            ``b = w_i / w_j`` (unchanged legacy path).
* ``SRSS``  ``R = sqrt(sum r_i^2)`` (unchanged legacy path).
* ``ABS``   ``R = sum |r_i|``.
* ``NRC10`` NRC Reg. Guide 1.92 ten-percent method:
            ``R^2 = sum r_i^2 + 2 sum_{i<j, close} |r_i r_j|`` where modes
            i, j are closely spaced when ``|f_j - f_i| <= 0.10 min(f_i, f_j)``.
* ``DSC``   Rosenblueth double sum:
            ``R^2 = sum_ij eps_ij r_i r_j``,
            ``eps_ij = 1 / (1 + ((w'_i - w'_j) / (z'_i w_i + z'_j w_j))^2)``,
            ``w' = w sqrt(1 - z^2)``, ``z' = z + 2 / (td w)`` (``td`` =
            ``dsc_td``, strong-motion duration, s).
* ``GMC``   Gupta: rigid-response coefficient
            ``alpha_i = ln(f_i / f1) / ln(f2 / f1)`` clamped to [0, 1]
            (``f1 = gmc_f1``, ``f2 = gmc_f2``, Hz); rigid part
            ``R_r = sum alpha_i r_i`` (ALGEBRAIC), periodic part
            ``r_p,i = sqrt(1 - alpha_i^2) r_i`` combined with CQC, and
            ``R = sqrt(R_r^2 + R_p^2)``.

``rigid_response = True`` applies the same periodic + rigid split (same
``alpha_i`` from ``gmc_f1`` / ``gmc_f2``) to any other method: the periodic
part is combined by the case's own method.  (GMC is CQC with the split
always on.)

Missing mass (``include_missing_mass``)
---------------------------------------
The residual mass not captured by the computed modes,
``f_mm = Sa(T=0) g (M iota - sum_i Gamma_i M phi_i)`` (ZPA = the spectral
value at zero period), is solved as an ordinary static load and treated as
a RIGID mode: it joins the rigid part ``R_r`` algebraically.  With no rigid
split ``R_r`` is the missing-mass response alone and
``R = sqrt(R_r^2 + R_p^2)``.

Load participation (results["modal"]["load_participation"])
-----------------------------------------------------------
Wilson's static / dynamic participation ratios (ETABS table "Modal Load
Participation Ratios"), in PERCENT, for each acceleration direction
(UX, UY, UZ: ``r = M iota``) and each load pattern (``r`` = the pattern's
equivalent nodal load vector, member fixed-end loads included):

* static  = ``sum_i (phi_i^T r)^2 / (w_i^2 m_i)  /  (r^T K^-1 r)``;
* dynamic = ``sum_i (phi_i^T r_m)^2 / m_i  /  (r_m^T M^-1 r_m)``,

with ``m_i = phi_i^T M phi_i`` and ``r_m`` the load restricted to the massed
DOFs (rigid-diaphragm slave loads condensed onto the master).  For a
pattern, ``phi_i^T r = w_i^2 phi_i^T M u_r`` (``u_r = K^-1 r`` the pattern's
static displacement) so only massed-DOF eigenvector values enter, and
``r^T K^-1 r = r . u_r``.  For accelerations the dynamic ratio equals the
cumulative modal mass participation.
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

MODAL_COMBO_METHODS = ("CQC", "SRSS", "ABS", "GMC", "NRC10", "DSC")
GMC_DEFAULT_F1 = 1.0        # Hz
GMC_DEFAULT_F2 = 33.0       # Hz
DSC_DEFAULT_TD = 20.0       # s
NRC10_TOL = 0.10
LOAD_PART_DIRECTIONS = (("UX", 1), ("UY", 2), ("UZ", 3))


# --------------------------------------------------------------------------- #
# pure numerics (hand-verifiable)
# --------------------------------------------------------------------------- #
def cqc_rho(omegas: Sequence[float], zeta: float) -> np.ndarray:
    """Der Kiureghian CQC correlation matrix (constant damping ``zeta``)."""
    n = len(omegas)
    rho = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            b = omegas[i] / omegas[j]
            num = 8.0 * zeta * zeta * (1.0 + b) * b ** 1.5
            den = (1.0 - b * b) ** 2 + 4.0 * zeta * zeta * b * (1.0 + b) ** 2
            rho[i, j] = rho[j, i] = num / den
    return rho


def dsc_eps(omegas: Sequence[float], zeta: float, td: float) -> np.ndarray:
    """Rosenblueth double-sum correlation matrix (duration ``td`` s)."""
    w = np.asarray(omegas, dtype=float)
    wd = w * math.sqrt(max(1.0 - zeta * zeta, 0.0))
    zp = zeta + 2.0 / (td * w)
    n = len(w)
    eps = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            x = (wd[i] - wd[j]) / (zp[i] * w[i] + zp[j] * w[j])
            eps[i, j] = eps[j, i] = 1.0 / (1.0 + x * x)
    return eps


def nrc10_close(omegas: Sequence[float], tol: float = NRC10_TOL) -> np.ndarray:
    """Off-diagonal 0/1 matrix flagging closely spaced mode pairs."""
    n = len(omegas)
    c = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            lo = min(omegas[i], omegas[j])
            if lo > 0.0 and abs(omegas[j] - omegas[i]) <= tol * lo + 1e-15:
                c[i, j] = c[j, i] = 1.0
    return c


def rigid_alphas(freqs: Sequence[float], f1: float, f2: float) -> np.ndarray:
    """Gupta rigid-response coefficients ``ln(f/f1)/ln(f2/f1)`` in [0, 1]."""
    out = []
    for f in freqs:
        if f <= f1:
            out.append(0.0)
        elif f >= f2:
            out.append(1.0)
        else:
            out.append(math.log(f / f1) / math.log(f2 / f1))
    return np.asarray(out, dtype=float)


class ModalCombiner:
    """Callable ``values -> combined positive response`` for one RS case.

    ``values`` holds the ``n`` signed modal responses, followed by
    ``n_rigid_extra`` extra rigid-mode responses (the missing-mass mode)
    which always join the rigid part algebraically.
    """

    def __init__(self, method: str, omegas: Sequence[float],
                 zeta: float = 0.05, f1: float = GMC_DEFAULT_F1,
                 f2: float = GMC_DEFAULT_F2, td: float = DSC_DEFAULT_TD,
                 rigid_response: bool = False, n_rigid_extra: int = 0):
        if method not in MODAL_COMBO_METHODS:
            raise ValueError(f"modal combination must be one of "
                             f"{'|'.join(MODAL_COMBO_METHODS)}, got {method!r}")
        self.method = method
        self.n = len(omegas)
        self.n_extra = int(n_rigid_extra)
        omegas = [float(w) for w in omegas]
        self.rigid = bool(rigid_response) or method == "GMC"
        base = "CQC" if method == "GMC" else method
        self.base = base
        if self.rigid:
            freqs = [w / (2.0 * math.pi) for w in omegas]
            self.alpha = rigid_alphas(freqs, f1, f2)
        else:
            self.alpha = np.zeros(self.n)
        self.periodic = np.sqrt(np.clip(1.0 - self.alpha ** 2, 0.0, None))
        if base == "CQC":
            self.mat = cqc_rho(omegas, zeta)
        elif base == "SRSS":
            self.mat = np.eye(self.n)
        elif base == "DSC":
            self.mat = dsc_eps(omegas, zeta, td)
        elif base == "NRC10":
            self.mat = nrc10_close(omegas)
        else:                                   # ABS
            self.mat = None

    def periodic_part(self, p: np.ndarray) -> float:
        if self.base == "ABS":
            return float(np.sum(np.abs(p)))
        if self.base == "NRC10":
            a = np.abs(p)
            return float(math.sqrt(max(float(p @ p + a @ self.mat @ a), 0.0)))
        return float(math.sqrt(max(float(p @ self.mat @ p), 0.0)))

    def split(self, values: Sequence[float]) -> Tuple[float, float]:
        """(rigid part R_r, periodic part R_p) of one quantity."""
        v = np.asarray(values, dtype=float)
        modal, extra = v[:self.n], v[self.n:]
        rr = float(self.alpha @ modal) + float(np.sum(extra))
        rp = self.periodic_part(self.periodic * modal)
        return rr, rp

    def __call__(self, values: Sequence[float]) -> float:
        rr, rp = self.split(values)
        if not self.rigid and self.n_extra == 0:
            return rp
        return math.sqrt(rr * rr + rp * rp)


def combine_values(values: Sequence[float], method: str,
                   omegas: Sequence[float], **kw) -> float:
    """One-shot convenience wrapper around :class:`ModalCombiner`."""
    return ModalCombiner(method, omegas, **kw)(values)


def is_legacy(rs) -> bool:
    """True when the case takes the unchanged pre-v1.13 CQC/SRSS path."""
    return (rs.combo_method in ("CQC", "SRSS")
            and not getattr(rs, "rigid_response", False)
            and not getattr(rs, "include_missing_mass", False))


# --------------------------------------------------------------------------- #
# engine glue (RS case)
# --------------------------------------------------------------------------- #
def combine_case_results(name: str, parts: list, comb: Callable):
    """Combine per-mode CaseResults quantity by quantity with ``comb``."""
    from .opensees_engine import CaseResults

    def comb_vecs(get) -> dict:
        first = get(parts[0])
        return {k: [comb([get(p)[k][i] for p in parts])
                    for i in range(len(v))]
                for k, v in first.items()}

    base = {k: comb([p.base[k] for p in parts])
            for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
    story = {s: {k: comb([p.story[s][k] for p in parts]) for k in s0}
             for s, s0 in parts[0].story.items()}
    member_stations: Dict[str, Dict[str, List[float]]] = {}
    for uid, st0 in parts[0].member_stations.items():
        entry: Dict[str, List[float]] = {"x": list(st0["x"])}
        for key in ("N", "V2", "V3", "T", "M2", "M3"):
            entry[key] = [comb([p.member_stations[uid][key][i]
                                for p in parts])
                          for i in range(len(st0[key]))]
        member_stations[uid] = entry
    return CaseResults(
        name=name,
        node_disp=comb_vecs(lambda r: r.node_disp),
        reactions=comb_vecs(lambda r: r.reactions),
        base=base,
        member_forces=comb_vecs(lambda r: r.member_forces),
        story=story,
        member_stations=member_stations,
    )


def missing_mass_loads(modal, mass_map: Dict[Tuple[int, int], float],
                       dof: int, gamma_key: str, sa_g: float
                       ) -> Dict[Tuple[int, int], float]:
    """Residual-mass load ``sa_g (M iota - sum Gamma_i M phi_i)``."""
    loads: Dict[Tuple[int, int], float] = {}
    for (t, d), m in mass_map.items():
        captured = sum(modal.participation[i - 1][gamma_key]
                       * modal.shapes[i][t][d - 1]
                       for i in range(1, len(modal.periods) + 1))
        loads[(t, d)] = sa_g * m * ((1.0 if d == dof else 0.0) - captured)
    return loads


def run_rs_extended(engine, rs, name: str, modal, per_mode: list,
                    omegas: List[float], spectrum, mass_map) -> object:
    """Non-legacy RS combination (ABS/GMC/NRC10/DSC, rigid, missing mass)."""
    from .opensees_engine import G_ACCEL, _interp_spectrum
    parts = list(per_mode)
    n_extra = 0
    if getattr(rs, "include_missing_mass", False):
        dof = 1 if rs.direction == "X" else 2
        gamma_key = "gamma_x" if rs.direction == "X" else "gamma_y"
        sa0 = _interp_spectrum(spectrum, 0.0) * rs.scale * G_ACCEL
        loads = missing_mass_loads(modal, mass_map, dof, gamma_key, sa0)
        parts.append(engine._modal_static(loads))
        n_extra = 1
    comb = ModalCombiner(
        rs.combo_method, omegas, zeta=rs.damping,
        f1=getattr(rs, "gmc_f1", GMC_DEFAULT_F1),
        f2=getattr(rs, "gmc_f2", GMC_DEFAULT_F2),
        td=getattr(rs, "dsc_td", DSC_DEFAULT_TD),
        rigid_response=getattr(rs, "rigid_response", False),
        n_rigid_extra=n_extra)
    return combine_case_results(name, parts, comb)


# --------------------------------------------------------------------------- #
# load participation ratios
# --------------------------------------------------------------------------- #
def _solve_load(engine, apply: Callable) -> Tuple[Dict[int, List[float]],
                                                  Dict[int, List[float]]]:
    """Linear static solve of one load vector; returns (u, r) per node.

    ``r`` is the EQUIVALENT nodal load vector actually assembled by
    OpenSees: the nodal loads (``nodeUnbalance``) minus every frame
    element's fixed-end resisting force at zero displacement (member span
    loads enter through ``eleLoad``; only frame segments carry them).
    """
    import openseespy.opensees as ops
    from .opensees_engine import _REUSE
    # the standard elastic build, reused across solves (v0.26 reuse token:
    # the next _elastic_domain call removes this pattern and ops.reset()s)
    asm = engine._elastic_domain()
    engine._seg_span_loads = {}
    engine._seg_fef = {}
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    _REUSE["loaded"] = True
    apply(asm)
    engine._setup_analysis(asm)
    if ops.analyze(1) != 0:
        raise RuntimeError("load-participation static solve failed")
    tags = list(asm.node_coords)
    u = {t: list(ops.nodeDisp(t)) for t in tags}
    r = {t: list(ops.nodeUnbalance(t)) for t in tags}
    for t in tags:
        for d in range(1, len(u[t]) + 1):
            ops.setNodeDisp(t, d, 0.0, "-commit")
    for etag in set(asm.seg_ele.values()):
        f = list(ops.eleForce(etag))
        nodes = list(ops.eleNodes(etag))
        ndf = len(f) // len(nodes) if nodes else 0
        for k, t in enumerate(nodes):
            if t not in r:
                continue
            for d in range(min(ndf, len(r[t]))):
                r[t][d] -= f[k * ndf + d]
    return u, r


def _condense_to_mass(asm, r: Dict[int, List[float]]
                      ) -> Dict[Tuple[int, int], float]:
    """Node loads -> massed DOFs (diaphragm slaves condensed onto master)."""
    full: Dict[Tuple[int, int], float] = {}
    for t, vec in r.items():
        for d, v in enumerate(vec, start=1):
            if v != 0.0:
                full[(t, d)] = full.get((t, d), 0.0) + v
    for s, master in getattr(asm, "masters", {}).items():
        cx, cy = asm.node_coords[master][0], asm.node_coords[master][1]
        for t in asm.story_nodes.get(s, ()):
            if t == master:
                continue
            fx = full.pop((t, 1), 0.0)
            fy = full.pop((t, 2), 0.0)
            mz = full.pop((t, 6), 0.0)
            x, y = asm.node_coords[t][0], asm.node_coords[t][1]
            full[(master, 1)] = full.get((master, 1), 0.0) + fx
            full[(master, 2)] = full.get((master, 2), 0.0) + fy
            full[(master, 6)] = (full.get((master, 6), 0.0) + mz
                                 + (x - cx) * fy - (y - cy) * fx)
    return {k: full.get(k, 0.0) for k in asm.mass_map}


def _ratios(modal, mass_map, u, r_mass: Dict[Tuple[int, int], float],
            rKr: float, phir: Optional[List[float]] = None
            ) -> Dict[str, float]:
    """Wilson static / dynamic ratios (percent) for one load vector."""
    stat_num = dyn_num = 0.0
    for i in range(1, len(modal.periods) + 1):
        phi = modal.shapes[i]
        w2 = (2.0 * math.pi / modal.periods[i - 1]) ** 2
        mi = sum(m * phi[t][d - 1] ** 2 for (t, d), m in mass_map.items())
        if mi <= 0.0:
            continue
        pm = sum(phi[t][d - 1] * r_mass[(t, d)] for (t, d) in mass_map)
        if phir is None:
            # phi^T r = w^2 phi^T M u_r  (massed-DOF eigenvector values only)
            pr = w2 * sum(m * phi[t][d - 1] * u[t][d - 1]
                          for (t, d), m in mass_map.items())
        else:
            pr = phir[i - 1]
        stat_num += pr * pr / (w2 * mi)
        dyn_num += pm * pm / mi
    rMr = sum(v * v / mass_map[k] for k, v in r_mass.items()
              if mass_map.get(k, 0.0) > 0.0)
    return {"static": 100.0 * stat_num / rKr if rKr > 1e-300 else 0.0,
            "dynamic": 100.0 * dyn_num / rMr if rMr > 1e-300 else 0.0}


def load_participation(engine, modal) -> dict:
    """Modal load participation ratios (percent) — see module docstring."""
    import openseespy.opensees as ops
    out: dict = {"acceleration": {}, "patterns": {}}
    if not modal.periods:
        return out
    asm = engine._asm if engine._asm is not None else engine._build()
    mass_map = dict(asm.mass_map)

    for key, dof in LOAD_PART_DIRECTIONS:
        loads = {(t, d): m for (t, d), m in mass_map.items() if d == dof}
        if not loads:
            out["acceleration"][key] = {"static": 0.0, "dynamic": 0.0}
            continue

        def apply(_asm, loads=loads):
            for (t, d), v in loads.items():
                vec = [0.0] * 6
                vec[d - 1] = v
                ops.load(t, *vec)

        u, _ = _solve_load(engine, apply)
        rKr = sum(v * u[t][d - 1] for (t, d), v in loads.items())
        r_mass = {k: loads.get(k, 0.0) for k in mass_map}
        phir = [sum(modal.shapes[i][t][d - 1] * v
                    for (t, d), v in loads.items())
                for i in range(1, len(modal.periods) + 1)]
        out["acceleration"][key] = _ratios(modal, mass_map, u, r_mass, rKr,
                                           phir)

    for pname in engine.model.patterns:
        try:
            u, r = _solve_load(
                engine, lambda a, p=pname: engine._apply_pattern(a, p, 1.0))
        except Exception:             # noqa: BLE001 - never break run()
            continue
        rKr = sum(rv * uv for t in r for rv, uv in zip(r[t], u[t]))
        r_mass = _condense_to_mass(asm, r)
        out["patterns"][pname] = _ratios(modal, mass_map, u, r_mass, rKr)
    return out
