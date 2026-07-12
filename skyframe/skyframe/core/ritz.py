"""Load-dependent Ritz vectors (WYD / Leger algorithm) — v0.24.

A SELF-CONTAINED numpy implementation on the SAME assembled elastic frame
stiffness the buckling module uses (:func:`skyframe.core.buckling.
assemble_elastic_stiffness` — engine-identical ``elasticBeamColumn`` 12x12
matrices) and the SAME diagonal mass rule the engine's ``_assign_mass``
applies (story masses lumped onto rigid-diaphragm masters — ux/uy plus the
rotational inertia ``m * (lx^2 + ly^2) / 12`` — or spread evenly over the
story nodes; explicit ``NodalMass`` entries on their own dofs).

Algorithm (the classic WYD sequence, per load-block column):

1. static start  ``x_1 = K^-1 f``  with ``f`` the spatial load —
   mass-proportional in X (``f = M r_x``), Y, or both (direction "XY"
   alternates the two chains);
2. recurrence    ``x_{i+1} = K^-1 M u_i``;
3. every candidate is Gram-Schmidt M-orthogonalized against ALL previous
   vectors (two passes, for numerical re-orthogonalization) and
   M-normalized; a candidate whose M-norm collapses below ``1e-8`` of its
   pre-orthogonalization norm ends that chain (the load-reachable subspace
   is exhausted — fewer than ``n`` vectors are returned, documented);
4. reduced eigenproblem ``K_r z = theta z`` with ``K_r = U^T K U`` (and
   ``M_r = I`` by construction) -> Ritz values ``theta ~ omega^2`` and
   Ritz vectors ``phi = U z``, sorted ascending.

Rigid diaphragms ARE supported (unlike the buckling module, which ignores
them): each rigid story gets a master at ``model.plan_center()`` and every
story node's (ux, uy, rz) is condensed onto the master's (ux, uy, rz) with
the exact rigid-body constraint rows

    ux_s = ux_M - (y_s - cy) * rz_M
    uy_s = uy_M + (x_s - cx) * rz_M
    rz_s = rz_M

assembled into a transformation ``T`` (full dofs x reduced dofs), so the
condensed pair is ``K_c = T^T K T`` / ``M_c = T^T M T`` — the same
elimination OpenSees' Transformation constraint handler performs for
``rigidDiaphragm``.

Modelling scope (documented, same honesty as buckling v0.10): frame members
only — shell regions and link elements are SKIPPED with a warning (their
stiffness is absent, so Ritz periods for wall/link-braced systems will be
too long); member end releases and rigid offsets are ignored with a
warning.  Supports mirror the engine: explicit ``PointSupport`` entries by
coordinate, else automatic base fixity at the lowest node elevation.

Units follow the model everywhere: kN, m, tonne, s.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .buckling import (_TOL, _pkey, _restraints, assemble_elastic_stiffness)
from .model import BuildingModel

Vec3 = Tuple[float, float, float]

RITZ_DIRECTIONS = ("X", "Y", "XY")


# --------------------------------------------------------------------------- #
# result object (ModalResults-compatible field names)
# --------------------------------------------------------------------------- #
@dataclass
class RitzResults:
    """Ritz-vector analysis results — same field names as ``ModalResults``.

    ``periods`` / ``frequencies`` are the approximate (upper-bound) modal
    periods from the reduced eigenproblem; ``participation`` carries the
    SAME per-entry shape as the engine's modal participation
    (``{mode, T, ux, gamma_x, uy, gamma_y, rz}``); ``shapes`` maps Ritz
    mode number (1-based) -> node tag -> 6 dof values (the module's own
    node tags; ``node_coords`` maps tag -> (x, y, z) for lookups).
    ``direction`` echoes the load direction that seeded the vectors.
    """

    periods: List[float]
    frequencies: List[float]
    participation: List[Dict[str, float]]
    shapes: Dict[int, Dict[int, List[float]]]
    direction: str = "X"
    node_coords: Dict[int, Vec3] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "periods": [float(t) for t in self.periods],
            "frequencies": [float(f) for f in self.frequencies],
            "participation": [dict(p) for p in self.participation],
            "shapes": {str(m): {str(t): [float(x) for x in v]
                                for t, v in sh.items()}
                       for m, sh in self.shapes.items()},
            "direction": self.direction,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------------- #
# core numpy algorithm (matrix-level; unit-testable by hand)
# --------------------------------------------------------------------------- #
def ritz_vectors(K: np.ndarray, M: np.ndarray, F: np.ndarray, n: int,
                 return_basis: bool = False):
    """WYD load-dependent Ritz vectors for the pair ``(K, M)``.

    ``K`` (SPD, free dofs only), ``M`` (symmetric PSD — a singular /
    partly-massless mass matrix is fine), ``F`` the spatial load block
    (one column per starting load vector; a 1-D array is one column —
    this is also the "user vector" entry point).  Returns
    ``(omega2, Phi)`` — Ritz values (ascending, rad^2/s^2) and
    M-orthonormal Ritz vectors (columns) — with ``len(omega2) <= n``
    (fewer when the M-reachable Krylov subspace of ``F`` is exhausted).
    ``return_basis=True`` additionally returns the raw (pre-rotation)
    M-orthonormal WYD basis ``U`` for hand verification.
    """
    K = np.asarray(K, dtype=float)
    M = np.asarray(M, dtype=float)
    F = np.asarray(F, dtype=float)
    if F.ndim == 1:
        F = F[:, None]
    if K.shape[0] == 0 or n <= 0:
        empty = np.zeros((K.shape[0], 0))
        return ((np.zeros(0), empty, empty) if return_basis
                else (np.zeros(0), empty))
    L = np.linalg.cholesky(K)

    def ksolve(b: np.ndarray) -> np.ndarray:
        return np.linalg.solve(L.T, np.linalg.solve(L, b))

    nb = F.shape[1]
    U: List[np.ndarray] = []
    chain: List[Optional[np.ndarray]] = [None] * nb   # last vector per chain
    alive = [True] * nb
    round_no = 0
    while len(U) < n and any(alive):
        for j in range(nb):
            if len(U) >= n or not alive[j]:
                continue
            if round_no == 0:
                x = ksolve(F[:, j])
            else:
                x = ksolve(M @ chain[j])
            norm0 = float(x @ (M @ x))
            if norm0 <= 0.0:
                alive[j] = False
                continue
            # two-pass M-Gram-Schmidt against all previous vectors
            for _ in range(2):
                for u in U:
                    x = x - u * float(u @ (M @ x))
            norm1 = float(x @ (M @ x))
            if norm1 <= 1e-16 * norm0:
                alive[j] = False              # subspace exhausted
                continue
            u = x / math.sqrt(norm1)
            U.append(u)
            chain[j] = u
        round_no += 1

    if not U:
        empty = np.zeros((K.shape[0], 0))
        return ((np.zeros(0), empty, empty) if return_basis
                else (np.zeros(0), empty))
    Umat = np.column_stack(U)
    Kr = Umat.T @ K @ Umat
    Kr = 0.5 * (Kr + Kr.T)
    theta, Z = np.linalg.eigh(Kr)             # ascending; M_r = I
    Phi = Umat @ Z
    if return_basis:
        return theta, Phi, Umat
    return theta, Phi


# --------------------------------------------------------------------------- #
# model-level analysis
# --------------------------------------------------------------------------- #
def _mass_and_masters(model: BuildingModel, tag_of: Dict[Vec3, int],
                      coords: Dict[int, Vec3], idx: Dict[int, int],
                      warn: List[str]):
    """Diagonal mass over (nodes + diaphragm masters) — engine-identical.

    Returns ``(m_diag, masters, slave_of, n_full)``: the diagonal mass
    vector over the EXTENDED full dof space (6 dofs per structural node,
    then 6 per master), ``masters`` mapping story name -> master dof base,
    and ``slave_of`` mapping a slave node tag -> (master base, dx, dy)
    lever arms from the plan center.
    """
    n_nodes = len(coords)
    story_masses = model.compute_story_masses()
    lx, ly = model.plan_extents()
    cx, cy = model.plan_center()

    # story node sets: structural nodes at the story elevation
    story_nodes: Dict[str, List[int]] = {}
    for s in model.stories:
        story_nodes[s.name] = [t for t, c in coords.items()
                               if abs(c[2] - s.elevation) < _TOL]

    masters: Dict[str, int] = {}      # story -> full-space dof base
    slave_of: Dict[int, Tuple[int, float, float]] = {}
    n_full = 6 * n_nodes
    for s in model.stories:
        if model.effective_diaphragm(s.name) != "rigid":
            continue
        slaves = story_nodes[s.name]
        if len(slaves) < 2:
            continue
        masters[s.name] = n_full
        for t in slaves:
            c = coords[t]
            slave_of[t] = (n_full, c[0] - cx, c[1] - cy)
        n_full += 6

    m_diag = np.zeros(n_full)
    for s in model.stories:
        m = story_masses.get(s.name, 0.0)
        if m <= 0.0:
            continue
        if s.name in masters:
            b = masters[s.name]
            m_diag[b + 0] += m
            m_diag[b + 1] += m
            m_diag[b + 5] += m * (lx * lx + ly * ly) / 12.0
        else:
            nodes = story_nodes[s.name]
            if not nodes:
                warn.append(f"story {s.name!r}: mass {m} t has no frame "
                            "nodes to lump onto; ignored")
                continue
            each = m / len(nodes)
            for t in nodes:
                b = 6 * idx[t]
                m_diag[b + 0] += each
                m_diag[b + 1] += each
    for nm in model.nodal_masses:
        t = tag_of.get(_pkey(nm.point))
        if t is None:
            warn.append(f"nodal mass at {tuple(nm.point)} has no frame "
                        "node; ignored")
            continue
        b = 6 * idx[t]
        for d, v in ((0, nm.mx), (1, nm.my), (2, nm.mz)):
            if v > 0.0:
                m_diag[b + d] += v
    return m_diag, masters, slave_of, n_full


def ritz_analysis(model: BuildingModel, n: int,
                  direction: str = "X") -> RitzResults:
    """Load-dependent Ritz vector analysis of the frame (v0.24).

    ``n`` requested vectors, ``direction`` in ``("X", "Y", "XY")`` — the
    mass-proportional spatial load(s) that seed the WYD sequence ("XY"
    alternates an X and a Y chain).  Returns a :class:`RitzResults`;
    ``len(periods)`` may be < ``n`` when the load-reachable subspace is
    smaller (e.g. more vectors than massed dofs were requested).
    """
    if direction not in RITZ_DIRECTIONS:
        raise ValueError(f"Ritz direction must be one of "
                         f"{RITZ_DIRECTIONS}, got {direction!r}")
    if n <= 0:
        raise ValueError(f"Ritz vector count must be >= 1, got {n}")
    warn: List[str] = []
    if model.shells:
        warn.append(f"{len(model.shells)} shell region(s) skipped "
                    "(frame-only Ritz stiffness)")
    if model.links:
        warn.append(f"{len(model.links)} link element(s) skipped")
    if any(m.release_tokens() for m in model.members):
        warn.append("member end releases ignored (members treated "
                    "continuous)")

    K_nodes, tag_of, coords, _ele, asm_warn = \
        assemble_elastic_stiffness(model)
    warn.extend(asm_warn)
    if not coords:
        return RitzResults([], [], [], {}, direction, {},
                           warn + ["no frame members to analyse"])
    restr = _restraints(model, tag_of, coords)
    tags_sorted = sorted(coords)
    idx = {t: i for i, t in enumerate(tags_sorted)}

    m_diag, masters, slave_of, n_full = _mass_and_masters(
        model, tag_of, coords, idx, warn)
    K_full = np.zeros((n_full, n_full))
    nd = K_nodes.shape[0]
    K_full[:nd, :nd] = K_nodes

    # ---- reduced dof numbering + rigid-diaphragm transformation T ----------
    # reduced dofs: every unrestrained node dof that is NOT a slaved
    # (ux, uy, rz) of a rigid story, plus each master's (ux, uy, rz).
    red_of: Dict[int, int] = {}       # full dof -> reduced dof (own dofs)
    red_kind: List[str] = []          # reduced dof -> "ux"|"uy"|... labels
    nred = 0
    for t in tags_sorted:
        r = restr[t]
        b = 6 * idx[t]
        for d in range(6):
            if r[d]:
                continue
            if t in slave_of and d in (0, 1, 5):
                continue                       # condensed onto the master
            red_of[b + d] = nred
            red_kind.append(("ux", "uy", "uz", "rx", "ry", "rz")[d])
            nred += 1
    master_red: Dict[int, Tuple[int, int, int]] = {}
    for s_name, b in masters.items():
        master_red[b] = (nred, nred + 1, nred + 2)      # ux, uy, rz
        red_of[b + 0] = nred
        red_of[b + 1] = nred + 1
        red_of[b + 5] = nred + 2
        red_kind.extend(["ux", "uy", "rz"])
        nred += 3
    if nred == 0:
        return RitzResults([], [], [], {}, direction, coords,
                           warn + ["all DOFs restrained"])

    T = np.zeros((n_full, nred))
    for full_dof, red_dof in red_of.items():
        T[full_dof, red_dof] = 1.0
    for t, (mb, dx, dy) in slave_of.items():
        rux, ruy, rrz = master_red[mb]
        b = 6 * idx[t]
        r = restr[t]
        if not r[0]:
            T[b + 0, rux] = 1.0
            T[b + 0, rrz] = -dy
        if not r[1]:
            T[b + 1, ruy] = 1.0
            T[b + 1, rrz] = dx
        if not r[5]:
            T[b + 5, rrz] = 1.0

    Kc = T.T @ K_full @ T
    Kc = 0.5 * (Kc + Kc.T)
    Mc = T.T @ (m_diag[:, None] * T)
    Mc = 0.5 * (Mc + Mc.T)

    # ---- spatial load block: f = M r (mass-proportional) -------------------
    kinds = np.array(red_kind)
    r_x = (kinds == "ux").astype(float)
    r_y = (kinds == "uy").astype(float)
    r_z = (kinds == "rz").astype(float)
    cols = {"X": [r_x], "Y": [r_y], "XY": [r_x, r_y]}[direction]
    F = np.column_stack([Mc @ r for r in cols])

    theta, Phi = ritz_vectors(Kc, Mc, F, n)
    if len(theta) < n:
        warn.append(f"only {len(theta)} Ritz vector(s) available for the "
                    f"{direction} load (requested {n}): the load-reachable "
                    "subspace is exhausted")
    periods = [2.0 * math.pi / math.sqrt(th) for th in theta]
    frequencies = [1.0 / t for t in periods]

    # ---- participation (engine ModalResults shape) --------------------------
    totals = {"ux": float(r_x @ (Mc @ r_x)), "uy": float(r_y @ (Mc @ r_y)),
              "rz": float(r_z @ (Mc @ r_z))}
    participation: List[Dict[str, float]] = []
    for i, Tn in enumerate(periods, start=1):
        phi = Phi[:, i - 1]
        den = float(phi @ (Mc @ phi))           # = 1 (M-orthonormal)
        entry: Dict[str, float] = {"mode": i, "T": float(Tn)}
        for key, r in (("ux", r_x), ("uy", r_y), ("rz", r_z)):
            gamma_key = {"ux": "gamma_x", "uy": "gamma_y"}.get(key)
            if den <= 0.0 or totals[key] <= 0.0:
                entry[key] = 0.0
                if gamma_key:
                    entry[gamma_key] = 0.0
                continue
            num = float(phi @ (Mc @ r))
            entry[key] = (num * num / den) / totals[key]
            if gamma_key:
                entry[gamma_key] = num / den
        participation.append(entry)

    # ---- node shapes (full space, structural nodes only) --------------------
    shapes: Dict[int, Dict[int, List[float]]] = {}
    for i in range(len(periods)):
        full = T @ Phi[:, i]
        sh: Dict[int, List[float]] = {}
        for t in tags_sorted:
            b = 6 * idx[t]
            sh[t] = [float(v) for v in full[b:b + 6]]
        shapes[i + 1] = sh

    return RitzResults(periods, frequencies, participation, shapes,
                       direction, dict(coords), warn)
