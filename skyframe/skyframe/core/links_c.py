"""Wave-C link properties (CONTRACT "Wave C links: coupled linear,
multilinear elastic per DOF, multilinear plastic hysteresis options, HDR,
velocity-dependent friction, T/C friction per direction, effective
(linear) stiffness and damping").

Pure Python (keys, validation, local axes, the coupled-stiffness spring
decomposition and the exact force laws used by tests, FNA and UI
previews).  The OpenSees builders live in :mod:`skyframe.engine.links_c`;
this module does NOT import :mod:`skyframe.core.model` (model.py imports
it through one-line hooks).

Link LOCAL axes (every Wave-C type, ETABS convention): ``U1`` = the link
axis i -> j (``+Z`` for a zero-length link); a vertical / zero-length link
has ``U2 = +X`` and ``U3 = U1 x U2`` (``+Y`` when U1 points up); any other
link has ``U2`` in the vertical plane through the axis pointing up and
``U3 = U1 x U2`` (horizontal).  ``R1..R3`` rotate about ``U1..U3``.

New ``LinkMember.link_type`` values (:data:`C_LINK_TYPES`):

``linear_coupled`` {K}
    ``K`` = symmetric positive semi-definite 6 x 6 stiffness (row-major
    nested list, local U1..R3) acting on the relative deformation
    ``d = [u_j(at i) - u_i ; theta_j - theta_i]`` (j's rigid-body motion
    carried to node i, so a finite-length link is in moment equilibrium).
    OpenSees has no general-matrix element, so the matrix is realised
    EXACTLY by springs in parallel (:func:`decompose`): ``rank(A)`` line
    springs (axial springs along a line ``e`` through a point ``r``,
    contributing ``k [e; r x e][e; r x e]^T``) plus up to three pure
    torsional springs (``k [0; a][0; a]^T``), each an ``elasticBeamColumn``
    whose ``-jntOffset`` places it on its line.  Realisable iff the trace
    of the U-R coupling block ``K[0:3, 3:6]`` is zero (Huang & Schimmels;
    the trace is invariant under the choice of reference point) — every
    shear/rocking and axial/rocking coupling qualifies; an axial-torsion
    (screw) coupling ``K[0, 3] != 0`` does not and is rejected.

``multilinear_elastic`` {curves, k_lin?}
    ETABS MultiLinear Elastic with EACH DOF defined separately:
    ``curves = {"U1": [[d, F], ...], ..., "R3": [...]}`` (the
    :mod:`skyframe.core.springprops` curve rules: all d > 0 = mirrored,
    else the curve passes through [0, 0]); DOFs without a curve are
    linear ``k_lin[i]`` (6-list, optional, default 0 = free).  OpenSees
    ``ElasticMultiLinear`` per DOF (loads and unloads on the curve).

``multilinear_degrading`` / ``multilinear_isotropic`` /
``multilinear_concrete`` / ``multilinear_brb`` {points, points_neg?, ...}
    Multilinear plastic hysteresis options (the Kinematic / Takeda / Pivot
    rules are the B11 types).  Layout follows ``plastic_wen``: a link with
    a horizontal component acts axially (U1); a vertical / zero-length
    link carries the law on BOTH shear directions with elastic ``kv``.

    * degrading — OpenSees ``Hysteretic`` with pinch 1/1 and the damage /
      unloading-degradation knobs ``damage1`` (ductility), ``damage2``
      (energy), ``unload_beta`` (unloading stiffness ``k0 mu^-beta``;
      default 0.5, the "modified Takeda" exponent).
    * isotropic — bilinear backbone (exactly 2 points): ``Steel01`` with
      isotropic hardening calibrated so a reversal from the backbone's
      last point ``(d2, F2)`` yields exactly at ``-F2`` (the ETABS rule:
      the yield surface expands to the largest force reached); other
      amplitudes follow Steel01's 0.8-power growth law
      (:func:`isotropic_params`).
    * concrete — ``Hysteretic`` with the ETABS-hinge "concrete" pinching
      (``pinch_x = 0.8``, ``pinch_y = 0.2``) and optional damage.
    * brb — ``Steel4`` asymmetric kinematic hardening from the backbone:
      yield ``(d1, F1)`` (same magnitude both sides), tension / compression
      hardening ratios from the second point of each side, Menegotto-Pinto
      ``R0``; optional isotropic hardening ``iso_b`` (``-iso b_i rho_i
      b_l R_i l_yp`` with ``rho_i = iso_rho``, ``b_l = b_i/10``,
      ``R_i = 1``, ``l_yp = 1``).

``hdr_isolator`` {Gr, Kbulk, D1, D2, ts, tr, n, a1, a2, a3, b1, b2, b3,
c1, c2, c3, c4, kc?, phi_m?, ac?}
    High-damping rubber bearing: OpenSees ``HDR`` element (Grant, Fenves
    & Whittaker 2004 model, Kumar et al. implementation).  The elastic
    Grant component is ``F = a1 u + a2 u^3 + a3 u^5`` (verified); b1..b3
    shape the hysteretic component, c1..c4 its scragging degradation.
    Vertical or zero-length link (node i = lower node).

Extra OPTIONAL params on existing types (legacy files never carry them):

* friction (``fp_isolator``, ``friction_spring``, ``tc_fp_isolator``):
  ``mu_fast`` + ``rate`` -> ``frictionModel VelDependent`` with
  ``mu(v) = mu_fast - (mu_fast - mu) exp(-rate |v|)`` (``mu`` is the slow
  value; verified exact); adding ``n_slow`` / ``n_fast`` (and optional
  ``mu_max_fact``, default 2) -> ``VelNormalFrcDep`` with
  ``mu_slow = mu N^(n_slow - 1)``, ``mu_fast = mu_fast N^(n_fast - 1)``.
* ``tc_fp_isolator``: ``mu3`` / ``R3`` (U3-direction friction / radius;
  ``mu`` / ``R`` act along U2; uncoupled per direction — FNA only, OpenSees
  has no orthotropic friction bearing for direct integration) and ``mu_t`` (+ ``R_t``, default ``R``):
  friction in TENSION on the hold-down (needs ``kt > 0``).
* every nonlinear type: ``k_eff`` (6-list, local U1..R3) and optional
  ``c_eff`` (6-list): the link's EFFECTIVE linear stiffness / damping.

Effective properties (ETABS): when a link carries ``k_eff`` every LINEAR
analysis (linear static, modal / Ritz, response spectrum, linear time
history, buckling, steady state) replaces it by a linear link with
``k_eff`` (+ viscous ``c_eff``) per local DOF; NONLINEAR analyses
(pushover, nonlinear static, nonlinear time history) use the nonlinear
property; FNA keeps ``k_eff`` as the linear part in its basis.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

DOF_LABELS = ("U1", "U2", "U3", "R1", "R2", "R3")
ISOLATOR_DEFAULT_KV = 1.0e7          # same value as core.model
FP_DEFAULT_KINIT = 1.0e5

C_LINK_TYPES = ("linear_coupled", "multilinear_elastic",
                "multilinear_degrading", "multilinear_isotropic",
                "multilinear_concrete", "multilinear_brb", "hdr_isolator")
PLASTIC_TYPES = ("multilinear_degrading", "multilinear_isotropic",
                 "multilinear_concrete", "multilinear_brb")
#: link types that are LINEAR in every analysis
LINEAR_TYPES = ("linear_coupled",)
#: types that make static cases nonlinear (Newton)
STATIC_NONLINEAR = ("multilinear_elastic", *PLASTIC_TYPES, "hdr_isolator")
#: non-scalar params (nested lists / dicts), never float-coerced
NESTED_PARAMS = ("K", "curves", "k_lin", "k_eff", "c_eff")

HDR_REQUIRED = ("Gr", "Kbulk", "D1", "D2", "ts", "tr", "n", "a1", "a2",
                "a3", "b1", "b2", "b3", "c1", "c2", "c3", "c4")
LINK_PARAM_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "linear_coupled": (("K",), ()),
    "multilinear_elastic": (("curves",), ("k_lin",)),
    "multilinear_degrading": (("points",), ("points_neg", "damage1",
                                            "damage2", "unload_beta",
                                            "kv")),
    "multilinear_isotropic": (("points",), ("kv",)),
    "multilinear_concrete": (("points",), ("points_neg", "pinch_x",
                                           "pinch_y", "damage1", "damage2",
                                           "unload_beta", "kv")),
    "multilinear_brb": (("points",), ("points_neg", "R0", "iso_b",
                                      "iso_rho", "kv")),
    "hdr_isolator": (HDR_REQUIRED, ("kc", "phi_m", "ac")),
}
DEFAULTS: Dict[str, Dict[str, float]] = {
    "multilinear_degrading": {"damage1": 0.0, "damage2": 0.0,
                              "unload_beta": 0.5, "pinch_x": 1.0,
                              "pinch_y": 1.0},
    "multilinear_concrete": {"damage1": 0.0, "damage2": 0.0,
                             "unload_beta": 0.0, "pinch_x": 0.8,
                             "pinch_y": 0.2},
    "multilinear_brb": {"R0": 20.0, "iso_b": 0.0, "iso_rho": 1.0},
    "hdr_isolator": {"kc": 10.0, "phi_m": 0.5, "ac": 1.0},
}
FRICTION_TYPES = ("fp_isolator", "friction_spring", "tc_fp_isolator")
FRICTION_KEYS = ("mu_fast", "rate", "n_slow", "n_fast", "mu_max_fact")
TC_KEYS = ("mu3", "R3", "mu_t", "R_t")
EFF_KEYS = ("k_eff", "c_eff")
MU_MAX_FACT_DEFAULT = 2.0
STEEL4_R1, STEEL4_R2 = 0.925, 0.15


# ===================================================================== keys
def extend_param_keys(table: Dict[str, Tuple[Tuple[str, ...],
                                             Tuple[str, ...]]]) -> None:
    """Called ONCE by core.model on its ``_LINK_PARAM_KEYS``: adds the
    Wave-C types and the new optional keys of existing types."""
    for t, (req, opt) in LINK_PARAM_KEYS.items():
        table[t] = (req, opt + (EFF_KEYS if t not in LINEAR_TYPES else ()))
    for t in list(table):
        if t in C_LINK_TYPES:
            continue
        req, opt = table[t]
        extra = EFF_KEYS
        if t in FRICTION_TYPES:
            extra = extra + FRICTION_KEYS
        if t == "tc_fp_isolator":
            extra = extra + TC_KEYS
        table[t] = (req, opt + tuple(k for k in extra if k not in opt))


def _isnum(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def coerce(key: str, v):
    """Canonical JSON copy of a nested param (floats inside); a malformed
    value is returned unchanged for the model validation to reject."""
    try:
        if key == "K":
            return [[float(x) for x in row] for row in v]
        if key == "curves":
            return {str(d): [[float(p[0]), float(p[1])] for p in pts]
                    for d, pts in v.items()}
        return [float(x) for x in v]                  # 6-lists
    except (TypeError, ValueError, AttributeError, IndexError):
        return v


def _six(where: str, key: str, v, nonneg: bool = True) -> List[float]:
    if (not isinstance(v, (list, tuple)) or len(v) != 6
            or not all(_isnum(x) for x in v)):
        raise ValueError(f"{where}: {key} must be a list of 6 finite "
                         "numbers (U1 U2 U3 R1 R2 R3)")
    if nonneg and any(x < 0.0 for x in v):
        raise ValueError(f"{where}: {key} entries must be >= 0")
    return [float(x) for x in v]


# ================================================================ geometry
def local_axes(pi: Sequence[float], pj: Sequence[float]) -> np.ndarray:
    """3x3 rows = local 1/2/3 unit vectors (global components)."""
    d = np.asarray(pj, float) - np.asarray(pi, float)
    L = float(np.linalg.norm(d))
    if L < 1e-9:
        return np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0],
                         [0.0, 1.0, 0.0]])
    x = d / L
    if math.hypot(x[0], x[1]) <= 1e-6:
        y = np.array([1.0, 0.0, 0.0])
    else:
        z = np.array([0.0, 0.0, 1.0])
        y = z - (z @ x) * x
        y = y / np.linalg.norm(y)
    return np.array([x, y, np.cross(x, y)])


def is_axial(pi, pj) -> bool:
    """True when a plastic type acts axially (horizontal component)."""
    return abs(pj[0] - pi[0]) + abs(pj[1] - pi[1]) > 1e-6


def global_K(K_local, pi, pj) -> np.ndarray:
    """6x6 local K -> global (about node i)."""
    R = local_axes(pi, pj)
    T = np.zeros((6, 6))
    T[:3, :3] = R
    T[3:, 3:] = R
    K = np.asarray(K_local, float)
    return T.T @ K @ T


def _zero_diag_basis(T: np.ndarray) -> np.ndarray:
    """Orthonormal Q with diag(Q^T T Q) = 0 for a symmetric zero-trace T."""
    r = T.shape[0]
    if r == 0:
        return np.zeros((0, 0))
    if r == 1:
        return np.eye(1)
    w, V = np.linalg.eigh(T)
    if r == 2:
        g1 = (V[:, 0] + V[:, 1]) / math.sqrt(2.0)
        g2 = (V[:, 0] - V[:, 1]) / math.sqrt(2.0)
        return np.column_stack([g1, g2])
    tmax, tmin = w[-1], w[0]
    if tmax - tmin <= 1e-300:
        return np.eye(r)
    phi = math.atan(math.sqrt(max(tmax, 0.0) / max(-tmin, 1e-300)))
    g1 = math.cos(phi) * V[:, -1] + math.sin(phi) * V[:, 0]
    h = -math.sin(phi) * V[:, -1] + math.cos(phi) * V[:, 0]
    H = np.column_stack([V[:, 1:-1], h])            # basis of g1-perp
    Q2 = _zero_diag_basis(H.T @ T @ H)
    return np.column_stack([g1, H @ Q2])


def decompose(Kg: np.ndarray, tol: float = 1e-9) -> Dict[str, list]:
    """Exact parallel-spring realisation of a global 6x6 stiffness about
    node i: ``{"lines": [(k, e, r)], "torsions": [(k, a)]}`` with
    ``sum k [e; r x e][e; r x e]^T + sum k [0; a][0; a]^T == Kg``.

    Raises ValueError when ``Kg`` is not symmetric positive semi-definite
    or its coupling block has a non-zero trace (screw coupling)."""
    K = 0.5 * (Kg + Kg.T)
    scale = max(float(np.max(np.abs(K))), 1e-300)
    if float(np.max(np.abs(Kg - Kg.T))) > 1e-9 * scale:
        raise ValueError("K must be symmetric")
    wK = np.linalg.eigvalsh(K)
    if wK[0] < -1e-9 * scale:
        raise ValueError("K must be positive semi-definite (eigenvalue "
                         f"{wK[0]:.6g})")
    A, B, D = K[:3, :3], K[:3, 3:], K[3:, 3:]
    nB = float(np.max(np.abs(B))) if B.size else 0.0
    ref = max(nB, math.sqrt(max(float(np.max(np.abs(A))), 0.0)
                            * max(float(np.max(np.abs(D))), 0.0)), 1e-300)
    if abs(float(np.trace(B))) > 1e-8 * ref:
        raise ValueError("the trace of the U-R coupling block K[0:3, 3:6] "
                         f"is {float(np.trace(B)):.6g}, not 0: a screw "
                         "(axial-torsion type) coupling cannot be realised "
                         "with OpenSees spring elements")
    lines: List[tuple] = []
    wA, VA = np.linalg.eigh(A)
    keep = wA > tol * max(float(np.max(np.abs(A))), 1e-300)
    S = D.copy()
    if np.any(keep):
        Ar, lam = VA[:, keep], wA[keep]
        Apinv = Ar @ np.diag(1.0 / lam) @ Ar.T
        X = Apinv @ B
        S = D - B.T @ X
        F = Ar @ np.diag(np.sqrt(lam))
        Xs = 0.5 * (X + X.T)
        Q = _zero_diag_basis(F.T @ Xs @ F)
        for f in (F @ Q).T:
            nf = float(np.linalg.norm(f))
            if nf <= 0.0:
                continue
            e = f / nf
            b = X.T @ e
            lines.append((nf * nf, e, np.cross(e, b)))
    torsions: List[tuple] = []
    wS, VS = np.linalg.eigh(0.5 * (S + S.T))
    for mu, a in zip(wS, VS.T):
        if mu > tol * scale:
            torsions.append((float(mu), a))
    return {"lines": lines, "torsions": torsions}


def realised(parts: Dict[str, list]) -> np.ndarray:
    """Re-assemble the 6x6 matrix of a decomposition (tests / checks)."""
    K = np.zeros((6, 6))
    for k, e, r in parts["lines"]:
        w = np.concatenate([e, np.cross(r, e)])
        K += k * np.outer(w, w)
    for k, a in parts["torsions"]:
        w = np.concatenate([np.zeros(3), a])
        K += k * np.outer(w, w)
    return K


# ============================================================== validation
def _g(prm: dict, ltype: str, key: str) -> float:
    return float(prm.get(key, DEFAULTS.get(ltype, {}).get(key, 0.0)))


def _backbone(where: str, pts, sign: float, key: str,
              n: Optional[Tuple[int, int]] = None) -> None:
    if (not isinstance(pts, (list, tuple)) or not pts
            or not all(isinstance(p, (list, tuple)) and len(p) == 2
                       and all(_isnum(v) for v in p) for p in pts)):
        raise ValueError(f"{where}: {key} must be a list of [d, F] pairs of "
                         "finite numbers")
    if n is not None and not n[0] <= len(pts) <= n[1]:
        raise ValueError(f"{where}: {key} needs {n[0]}..{n[1]} points")
    prev = 0.0
    for d, f in pts:
        if sign * d <= sign * prev:
            raise ValueError(f"{where}: {key} displacements must move "
                             "strictly away from 0")
        if sign * f <= 0.0:
            raise ValueError(f"{where}: {key} forces must be "
                             f"{'> 0' if sign > 0 else '< 0'}")
        prev = d


def validate_link(uid: str, ltype: str, prm: dict, pi, pj) -> None:
    """Value checks of a Wave-C type (keys already checked by the model)."""
    w = f"Link {uid} ({ltype})"
    L = math.dist(pi, pj)
    if ltype == "linear_coupled":
        K = prm["K"]
        if (not isinstance(K, (list, tuple)) or len(K) != 6
                or not all(isinstance(r, (list, tuple)) and len(r) == 6
                           and all(_isnum(x) for x in r) for r in K)):
            raise ValueError(f"{w}: K must be a 6x6 nested list of finite "
                             "numbers")
        Kl = np.asarray(K, float)
        if not np.any(Kl):
            raise ValueError(f"{w}: K must not be all zero")
        try:
            decompose(global_K(Kl, pi, pj))
        except ValueError as exc:
            raise ValueError(f"{w}: {exc}") from None
        return
    if ltype == "multilinear_elastic":
        from skyframe.core.springprops import _validate_curve
        cv = prm["curves"]
        if not isinstance(cv, dict) or set(cv) - set(DOF_LABELS):
            raise ValueError(f"{w}: curves must be {{dof: [[d, F], ...]}} "
                             f"with dof in {DOF_LABELS}")
        for d, pts in cv.items():
            _validate_curve(f"{w} curve {d}", pts)
        kl = _six(w, "k_lin", prm["k_lin"]) if "k_lin" in prm \
            else [0.0] * 6
        if not cv and not any(kl):
            raise ValueError(f"{w}: define at least one curve or k_lin > 0")
        for i, d in enumerate(DOF_LABELS):
            if d in cv and kl[i] > 0.0:
                raise ValueError(f"{w}: DOF {d} has both a curve and "
                                 "k_lin")
        _validate_eff(w, ltype, prm)
        return
    if ltype in PLASTIC_TYPES:
        nmax = 2 if ltype == "multilinear_isotropic" else (
            2 if ltype == "multilinear_brb" else 3)
        rng = (2, nmax)
        _backbone(w, prm["points"], 1.0, "points", rng)
        if "points_neg" in prm:
            _backbone(w, prm["points_neg"], -1.0, "points_neg", rng)
            if len(prm["points_neg"]) != len(prm["points"]):
                raise ValueError(f"{w}: points_neg must have as many points "
                                 "as points")
        pts = prm["points"]
        if pts[1][1] < pts[0][1] and ltype in ("multilinear_isotropic",
                                               "multilinear_brb"):
            raise ValueError(f"{w}: the post-yield slope must be >= 0")
        if ltype == "multilinear_brb" and "points_neg" in prm:
            n0 = prm["points_neg"][0]
            if abs(abs(n0[0]) - pts[0][0]) > 1e-9 * pts[0][0] or abs(
                    abs(n0[1]) - pts[0][1]) > 1e-9 * pts[0][1]:
                raise ValueError(f"{w}: a BRB yields at the same point "
                                 "on both sides (points_neg[0] = "
                                 "-points[0])")
            if n0 and prm["points_neg"][1][1] > prm["points_neg"][0][1]:
                raise ValueError(f"{w}: the compression post-yield slope "
                                 "must be >= 0")
        for key in ("damage1", "damage2", "unload_beta", "iso_b"):
            if key in prm and prm[key] < 0.0:
                raise ValueError(f"{w}: {key} must be >= 0")
        for key in ("pinch_x", "pinch_y"):
            if key in prm and not 0.0 < prm[key] <= 1.0:
                raise ValueError(f"{w}: {key} must be in (0, 1]")
        for key in ("R0", "iso_rho", "kv"):
            if key in prm and prm[key] <= 0.0:
                raise ValueError(f"{w}: {key} must be > 0")
        if "kv" in prm and is_axial(pi, pj) and L > 1e-9:
            raise ValueError(f"{w}: kv applies only to a vertical or "
                             "zero-length link (shear layout)")
        _validate_eff(w, ltype, prm)
        return
    if ltype == "hdr_isolator":
        for key in ("Gr", "Kbulk", "D2", "ts", "tr", "n"):
            if prm[key] <= 0.0:
                raise ValueError(f"{w}: {key} must be > 0")
        if not 0.0 <= prm["D1"] < prm["D2"]:
            raise ValueError(f"{w}: D1 must satisfy 0 <= D1 < D2")
        if abs(prm["n"] - round(prm["n"])) > 1e-9:
            raise ValueError(f"{w}: n (rubber layers) must be an integer")
        for key in ("kc", "phi_m", "ac"):
            if key in prm and prm[key] <= 0.0:
                raise ValueError(f"{w}: {key} must be > 0")
        if L > 1e-9 and is_axial(pi, pj):
            raise ValueError(f"{w}: the link axis must be vertical (or zero "
                             "length)")
        _validate_eff(w, ltype, prm)
        return


def _validate_eff(w: str, ltype: str, prm: dict) -> None:
    if "k_eff" in prm:
        k = _six(w, "k_eff", prm["k_eff"])
        if not any(k):
            raise ValueError(f"{w}: k_eff needs at least one entry > 0")
    if "c_eff" in prm:
        if "k_eff" not in prm:
            raise ValueError(f"{w}: c_eff needs k_eff (the effective linear "
                             "link)")
        _six(w, "c_eff", prm["c_eff"])


def validate_extra(uid: str, ltype: str, prm: dict,
                   length: float = 0.0) -> None:
    """New optional keys on EXISTING (pre-Wave-C) types."""
    w = f"Link {uid} ({ltype})"
    _validate_eff(w, ltype, prm)
    if ltype in FRICTION_TYPES and any(k in prm for k in FRICTION_KEYS):
        if "mu_fast" not in prm or "rate" not in prm:
            raise ValueError(f"{w}: velocity-dependent friction needs both "
                             "mu_fast and rate")
        if prm["mu_fast"] <= 0.0:
            raise ValueError(f"{w}: mu_fast must be > 0")
        if prm["rate"] <= 0.0:
            raise ValueError(f"{w}: rate must be > 0")
        for key in ("n_slow", "n_fast", "mu_max_fact"):
            if key in prm and prm[key] <= 0.0:
                raise ValueError(f"{w}: {key} must be > 0")
        if "mu_max_fact" in prm and not ("n_slow" in prm or "n_fast" in prm):
            raise ValueError(f"{w}: mu_max_fact applies to the normal-force "
                             "dependent model (n_slow / n_fast)")
    if ltype == "tc_fp_isolator":
        for key in TC_KEYS:
            if key in prm and prm[key] <= 0.0:
                raise ValueError(f"{w}: {key} must be > 0")
        if "R_t" in prm and "mu_t" not in prm:
            raise ValueError(f"{w}: R_t needs mu_t (friction in tension)")
        if "mu_t" in prm and not float(prm.get("kt", 0.0)) > 0.0:
            raise ValueError(f"{w}: friction in tension (mu_t) needs a "
                             "hold-down stiffness kt > 0")


# ================================================================= laws
def friction_mu(prm: dict, v: float, N: Optional[float] = None) -> float:
    """Friction coefficient at sliding velocity v (and normal force N for
    the normal-force dependent model) — the OpenSees friction-model law."""
    mu = float(prm["mu"])
    if "mu_fast" not in prm:
        return mu
    mf, a = float(prm["mu_fast"]), float(prm["rate"])
    if ("n_slow" in prm or "n_fast" in prm) and N is not None and N > 0.0:
        ns, nf = float(prm.get("n_slow", 1.0)), float(prm.get("n_fast", 1.0))
        cap = float(prm.get("mu_max_fact", MU_MAX_FACT_DEFAULT))
        mu = min(mu * N ** (ns - 1.0), cap * mu)
        mf = min(mf * N ** (nf - 1.0), cap * mf)
    return mf - (mf - mu) * math.exp(-a * abs(v))


def friction_model_args(prm: dict, tag: int,
                        mu_key: str = "mu") -> List:
    """``ops.frictionModel`` args of a friction link (``mu_key`` = the slow
    coefficient key: "mu", "mu3" or "mu_t")."""
    mu = float(prm[mu_key])
    if "mu_fast" not in prm:
        return ["Coulomb", tag, mu]
    ratio = float(prm["mu_fast"]) / float(prm["mu"])
    mf = ratio * mu                      # same fast/slow ratio per surface
    a = float(prm["rate"])
    if "n_slow" in prm or "n_fast" in prm:
        return ["VelNormalFrcDep", tag, mu, float(prm.get("n_slow", 1.0)),
                mf, float(prm.get("n_fast", 1.0)), a, 0.0, 0.0,
                float(prm.get("mu_max_fact", MU_MAX_FACT_DEFAULT))]
    return ["VelDependent", tag, mu, mf, a]


def hysteretic_args(prm: dict) -> List[float]:
    """Hysteretic envelope args (mom/rot pairs, + side then - side)."""
    pos = [[float(v) for v in p] for p in prm["points"]]
    neg = ([[float(v) for v in p] for p in prm["points_neg"]]
           if "points_neg" in prm else [[-d, -f] for d, f in pos])
    out: List[float] = []
    for d, f in pos:
        out += [f, d]
    for d, f in neg:
        out += [f, d]
    return out


def brb_params(prm: dict) -> dict:
    """Steel4 parameters of a multilinear_brb backbone."""
    (d1, F1), (d2, F2) = prm["points"]
    E0 = F1 / d1
    bt = (F2 - F1) / (d2 - d1) / E0
    if "points_neg" in prm:
        (n1, G1), (n2, G2) = prm["points_neg"]
        bc = (G2 - G1) / (n2 - n1) / E0
    else:
        bc = bt
    return {"fy": F1, "E0": E0, "bt": bt, "bc": bc,
            "R0": _g(prm, "multilinear_brb", "R0"),
            "iso_b": _g(prm, "multilinear_brb", "iso_b"),
            "iso_rho": _g(prm, "multilinear_brb", "iso_rho")}


def isotropic_params(prm: dict) -> dict:
    """Steel01 parameters of a bilinear isotropic link.

    Steel01 moves the reverse yield line by the factor
    ``s = 1 + a1 ((eps_max - eps_min)/(2 a2 eps_y))^0.8`` (verified); the
    ETABS isotropic rule (a reversal from the peak F(D) yields at -F(D))
    needs ``s = 1 + 2 b (D/eps_y - 1)``.  ``a1`` (``a2 = 1``) is chosen so
    both agree EXACTLY for an excursion to the backbone's last point
    ``d2``; other amplitudes follow Steel01's 0.8-power growth (see
    :func:`isotropic_shift`)."""
    (d1, F1), (d2, F2) = prm["points"]
    E0 = F1 / d1
    b = (F2 - F1) / (d2 - d1) / E0
    a = 2.0 * b * (d2 / d1 - 1.0) / (d2 / (2.0 * d1)) ** 0.8
    return {"fy": F1, "E0": E0, "b": b, "a1": a, "a2": 1.0, "a3": a,
            "a4": 1.0}


def isotropic_shift(prm: dict, D: float) -> Tuple[float, float]:
    """(ETABS target, Steel01) reverse-yield factors after a first
    excursion to D >= d1."""
    p = isotropic_params(prm)
    ey = p["fy"] / p["E0"]
    return (1.0 + 2.0 * p["b"] * (D / ey - 1.0),
            1.0 + p["a1"] * (D / (2.0 * ey)) ** 0.8)


def backbone_force(pts: Sequence[Sequence[float]], u: float) -> float:
    """Monotonic backbone through (0, 0) and ``pts`` (same-sign side);
    beyond the last point the last slope continues."""
    a = [(0.0, 0.0)] + [(float(p[0]), float(p[1])) for p in pts]
    for (d0, f0), (d1, f1) in zip(a, a[1:]):
        if abs(u) <= abs(d1):
            return f0 + (f1 - f0) * (u - d0) / (d1 - d0)
    (d0, f0), (d1, f1) = a[-2], a[-1]
    return f1 + (f1 - f0) / (d1 - d0) * (u - d1)


def hdr_elastic_force(prm: dict, u: float) -> float:
    """Grant-model elastic component ``a1 u + a2 u^3 + a3 u^5``."""
    return (float(prm["a1"]) * u + float(prm["a2"]) * u ** 3
            + float(prm["a3"]) * u ** 5)


def eff_of(prm: dict) -> Optional[Tuple[List[float], List[float]]]:
    """(k_eff, c_eff) 6-lists or None."""
    if "k_eff" not in prm:
        return None
    return ([float(x) for x in prm["k_eff"]],
            [float(x) for x in prm.get("c_eff", [0.0] * 6)])


def new_params_used(ltype: str, prm: dict) -> bool:
    """True when an EXISTING type carries a Wave-C friction / T/C key (its
    build is routed to engine.links_c)."""
    if ltype in FRICTION_TYPES and "mu_fast" in prm:
        return True
    return ltype == "tc_fp_isolator" and any(k in prm for k in TC_KEYS)


__all__ = ["C_LINK_TYPES", "PLASTIC_TYPES", "LINEAR_TYPES",
           "STATIC_NONLINEAR", "NESTED_PARAMS", "LINK_PARAM_KEYS",
           "DEFAULTS", "extend_param_keys", "coerce", "local_axes",
           "global_K", "decompose", "realised", "validate_link",
           "validate_extra", "friction_mu", "friction_model_args",
           "hysteretic_args", "brb_params", "isotropic_params",
           "backbone_force", "hdr_elastic_force", "eff_of",
           "new_params_used"]
