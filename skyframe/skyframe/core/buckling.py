"""Linear (eigenvalue) buckling analysis — v0.10.

A SELF-CONTAINED numpy implementation, deliberately independent of OpenSees
for reliability.  It assembles the global elastic stiffness ``K`` and the
consistent geometric stiffness ``Kg`` of the 3D frame (``elasticBeamColumn``
members only), computes the member axial forces under a reference gravity
state by a linear static solve, and solves the generalized eigenproblem

    K phi = lambda (-Kg) phi

for the smallest positive load factors ``lambda`` (the critical multipliers on
the reference gravity load) and their buckling mode shapes.

Modelling scope (v0.10, documented):

* only ``elasticBeamColumn`` frame members enter K/Kg; shell regions and link
  elements are SKIPPED with a warning;
* rigid diaphragm constraints are IGNORED — the frame is analysed bare (the
  standard "no rigid floor" buckling assumption);
* member end releases are IGNORED (the member is treated as fully continuous)
  with a warning — release condensation is not applied in v0.10;
* reference gravity loads are reduced to equivalent nodal loads: nodal loads
  act directly; member distributed/point gravity loads and self-weight are
  lumped to their end nodes (exact for column axial force by vertical
  equilibrium); area/story/thermal loads are skipped with a warning.

The 12x12 elastic beam stiffness and the consistent geometric stiffness use
the same local-axis convention as the OpenSees engine (local x from i->j,
``vecxz = (dy, -dx, 0)`` for non-vertical members, ``(1, 0, 0)`` for vertical
members), so K matches the engine's ``elasticBeamColumn`` to machine
precision.

Units follow the model everywhere: kN, m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .model import BuildingModel, FrameMember

_TOL = 1e-6
Vec3 = Tuple[float, float, float]


# --------------------------------------------------------------------------- #
# small vector / element helpers (mirrors skyframe.engine.opensees_engine)
# --------------------------------------------------------------------------- #
def _unit(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _pkey(p: Vec3) -> Vec3:
    """Node dedup key: coordinates rounded to 1e-6."""
    return (round(float(p[0]), 6), round(float(p[1]), 6), round(float(p[2]), 6))


def _local_axes(member: FrameMember) -> Tuple[Vec3, Vec3, Vec3]:
    """Local axis triad (x, y, z) — same convention as the OpenSees engine."""
    d = (member.pj[0] - member.pi[0],
         member.pj[1] - member.pi[1],
         member.pj[2] - member.pi[2])
    x = _unit(d)
    vertical = math.hypot(d[0], d[1]) < _TOL
    vecxz: Vec3 = (1.0, 0.0, 0.0) if vertical else _unit((x[1], -x[0], 0.0))
    y = _unit(_cross(vecxz, x))
    z = _cross(x, y)
    ang = getattr(member, "angle", 0.0) or 0.0
    if abs(ang) > 1e-12:
        a = math.radians(ang)
        ca, sa = math.cos(a), math.sin(a)
        y2 = (ca * y[0] + sa * z[0], ca * y[1] + sa * z[1],
              ca * y[2] + sa * z[2])
        z2 = (ca * z[0] - sa * y[0], ca * z[1] - sa * y[1],
              ca * z[2] - sa * y[2])
        y, z = _unit(y2), _unit(z2)
    return x, y, z


def _transform(x: Vec3, y: Vec3, z: Vec3) -> np.ndarray:
    """12x12 transform T with local = T @ global (block-diagonal 3x3 R)."""
    R = np.array([x, y, z])
    T = np.zeros((12, 12))
    for i in range(4):
        T[3 * i:3 * i + 3, 3 * i:3 * i + 3] = R
    return T


def _local_elastic(E: float, G: float, A: float, Iy: float, Iz: float,
                   J: float, L: float) -> np.ndarray:
    """12x12 local elastic stiffness of a 3D Euler beam (engine-identical)."""
    k = np.zeros((12, 12))
    ea, gj = E * A / L, G * J / L
    k[np.ix_((0, 6), (0, 6))] = ea * np.array([[1.0, -1.0], [-1.0, 1.0]])
    k[np.ix_((3, 9), (3, 9))] = gj * np.array([[1.0, -1.0], [-1.0, 1.0]])
    for (dofs, EI, s) in (((1, 5, 7, 11), E * Iz, 1.0),
                          ((2, 4, 8, 10), E * Iy, -1.0)):
        c = EI / L ** 3
        kb = c * np.array([
            [12.0, s * 6 * L, -12.0, s * 6 * L],
            [s * 6 * L, 4 * L * L, -s * 6 * L, 2 * L * L],
            [-12.0, -s * 6 * L, 12.0, -s * 6 * L],
            [s * 6 * L, 2 * L * L, -s * 6 * L, 4 * L * L]])
        k[np.ix_(dofs, dofs)] += kb
    return k


def _local_geometric(N: float, L: float) -> np.ndarray:
    """12x12 consistent geometric stiffness for axial force ``N``.

    ``N`` is tension-positive.  The transverse/rotational sub-matrices in the
    two bending planes use the same sign convention (``s``) as the elastic
    stiffness so K and Kg are consistent.  Axial and torsional geometric
    terms are omitted (the standard beam-column form; they do not affect
    flexural buckling).
    """
    kg = np.zeros((12, 12))
    c = N / L
    for (dofs, s) in (((1, 5, 7, 11), 1.0), ((2, 4, 8, 10), -1.0)):
        m = c * np.array([
            [6.0 / 5.0, s * L / 10.0, -6.0 / 5.0, s * L / 10.0],
            [s * L / 10.0, 2.0 * L * L / 15.0, -s * L / 10.0, -L * L / 30.0],
            [-6.0 / 5.0, -s * L / 10.0, 6.0 / 5.0, -s * L / 10.0],
            [s * L / 10.0, -L * L / 30.0, -s * L / 10.0, 2.0 * L * L / 15.0]])
        kg[np.ix_(dofs, dofs)] += m
    return kg


# --------------------------------------------------------------------------- #
# result object
# --------------------------------------------------------------------------- #
@dataclass
class BucklingResult:
    """Linear buckling results for one reference gravity state.

    ``factors`` are the sorted (ascending) positive critical load multipliers
    ``lambda`` on the reference gravity.  ``modes`` maps mode number (1-based)
    to per-node 6-dof buckling mode shapes.  ``node_coords`` (not serialised
    in the standard dict shape) maps node tag -> (x, y, z) for lookups.

    ``base_case`` (v0.25): name of the static case whose converged axial
    forces formed the fixed base geometric stiffness (see
    :func:`buckling_analysis` ``base_N``), or ``None`` for the classic
    single-load-set problem.  With a base state, ``factors`` are the
    critical multipliers ON THE BUCKLING ``gravity`` LOADS ONLY — the base
    state is held constant (two-load-set formulation).
    """

    factors: List[float]
    modes: Dict[int, Dict[int, List[float]]]
    gravity: Dict[str, float]
    node_coords: Dict[int, Vec3] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    base_case: Optional[str] = None                            # v0.25

    def to_dict(self) -> dict:
        return {
            "factors": [float(f) for f in self.factors],
            "modes": {str(m): {str(t): [float(x) for x in v]
                               for t, v in sh.items()}
                      for m, sh in self.modes.items()},
            "gravity": dict(self.gravity),
            "warnings": list(self.warnings),
            "base_case": self.base_case,
        }


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _node_map(model: BuildingModel) -> Tuple[Dict[Vec3, int], Dict[int, Vec3]]:
    """Assign integer tags to the deduped frame-member endpoint nodes."""
    tag_of: Dict[Vec3, int] = {}
    coords: Dict[int, Vec3] = {}
    nxt = 1
    for m in model.members:
        for p in (m.pi, m.pj):
            key = _pkey(p)
            if key not in tag_of:
                tag_of[key] = nxt
                coords[nxt] = (float(p[0]), float(p[1]), float(p[2]))
                nxt += 1
    return tag_of, coords


def _restraints(model: BuildingModel, tag_of: Dict[Vec3, int],
                coords: Dict[int, Vec3]) -> Dict[int, Tuple[int, ...]]:
    """Per-node 6-dof restraint flags (1 = fixed) from supports / base fixity.

    Explicit ``PointSupport`` entries win (matched by coordinate).  When the
    model has NO explicit supports, automatic base fixity is applied to every
    node at the lowest elevation (fully fixed for ``base_fixity == "fixed"``,
    else pinned = translations only).
    """
    restr: Dict[int, Tuple[int, ...]] = {t: (0,) * 6 for t in coords}
    if model.supports:
        for sup in model.supports:
            key = _pkey(sup.point)
            t = tag_of.get(key)
            if t is not None:
                restr[t] = tuple(int(bool(v)) for v in sup.restraints)
    else:
        if coords:
            zmin = min(c[2] for c in coords.values())
            fixed = model.base_fixity != "pinned"
            base = (1, 1, 1, 1, 1, 1) if fixed else (1, 1, 1, 0, 0, 0)
            for t, c in coords.items():
                if abs(c[2] - zmin) < _TOL:
                    restr[t] = base
    return restr


def _member_props(model: BuildingModel, m: FrameMember
                  ) -> Optional[Tuple[float, float, float, float,
                                      float, float]]:
    """(E, G, A, Iy, Iz, J) with stiffness modifiers, or None if unresolved."""
    sec = model.sections.get(m.section)
    if sec is None:
        return None
    mat = model.materials.get(sec.material)
    if mat is None:
        return None
    E = mat.E
    G = mat.G
    A = sec.A * sec.mod_A
    Iz = sec.I33 * sec.mod_I33      # major axis (bending about local z)
    Iy = sec.I22 * sec.mod_I22      # minor axis (bending about local y)
    J = sec.J * sec.mod_J
    return E, G, A, Iy, Iz, J


def _load_global_components(ml) -> Optional[Vec3]:
    """Unit global direction (gx, gy, gz) of a member load, or None.

    ``None`` for ``local_y`` (needs member axes — skipped in the gravity
    reduction; buckling reference is a gravity state).
    """
    d = ml.direction
    if d == "gravity":
        return (0.0, 0.0, -1.0)
    if d == "global_z":
        return (0.0, 0.0, 1.0)
    if d == "global_x":
        return (1.0, 0.0, 0.0)
    if d == "global_y":
        return (0.0, 1.0, 0.0)
    return None      # local_y


def assemble_elastic_stiffness(model: BuildingModel
                               ) -> Tuple[np.ndarray, Dict[Vec3, int],
                                          Dict[int, Vec3], List, List[str]]:
    """Assemble the global elastic stiffness of the bare frame (v0.24).

    Extracted from :func:`buckling_analysis` so the load-dependent Ritz
    module (:mod:`skyframe.core.ritz`) can reuse the SAME engine-identical
    ``elasticBeamColumn`` stiffness.  Returns ``(K, tag_of, coords, ele,
    warnings)`` where ``K`` is the ``6 * n_nodes`` square global stiffness
    over the deduped frame endpoint nodes (tag order = sorted tags, 6 dofs
    per node) and ``ele`` caches per-member ``(member, T, dofs, EA/L, L)``
    for the geometric-stiffness pass.  Same modelling scope as buckling:
    frame members only (shells / links / releases are the CALLER's
    responsibility to warn about).
    """
    warn: List[str] = []
    tag_of, coords = _node_map(model)
    if not coords:
        return np.zeros((0, 0)), tag_of, coords, [], warn
    tags_sorted = sorted(coords)
    idx = {t: i for i, t in enumerate(tags_sorted)}
    ndof = 6 * len(coords)
    K = np.zeros((ndof, ndof))
    ele: List[Tuple[FrameMember, np.ndarray, np.ndarray, float, float]] = []
    for m in model.members:
        props = _member_props(model, m)
        if props is None:
            warn.append(f"member {m.uid}: unresolved section/material, skipped")
            continue
        E, G, A, Iy, Iz, J = props
        L = m.length
        x, y, z = _local_axes(m)
        T = _transform(x, y, z)
        kl = _local_elastic(E, G, A, Iy, Iz, J, L)
        kg = T.T @ kl @ T
        ti, tj = tag_of[_pkey(m.pi)], tag_of[_pkey(m.pj)]
        dofs = ([6 * idx[ti] + k for k in range(6)]
                + [6 * idx[tj] + k for k in range(6)])
        K[np.ix_(dofs, dofs)] += kg
        ele.append((m, T, np.array(dofs), E * A / L, L))
    return K, tag_of, coords, ele, warn


def buckling_analysis(model: BuildingModel, gravity: Dict[str, float],
                      num_modes: int = 6,
                      base_N: Optional[Dict[str, float]] = None,
                      base_label: Optional[str] = None) -> BucklingResult:
    """Linear buckling analysis under a reference gravity state.

    ``gravity`` maps load-pattern name -> factor (the reference load whose
    critical multiplier is sought).  Returns a :class:`BucklingResult` with
    the ``num_modes`` smallest positive load factors and their mode shapes.

    ``base_N`` (v0.25, buckling from a stressed/staged state): optional
    member uid -> CONSTANT axial force (kN, TENSION POSITIVE — the member
    station/`ul[6]-ul[0]` convention) of a pre-existing base stress state.
    The eigenproblem becomes the standard TWO-LOAD-SET form

        (K + Kg(N_base)) phi = lambda (-Kg(N_gravity)) phi

    where ``Kg(N_base)`` is assembled from ``base_N`` and HELD FIXED while
    ``N_gravity`` still comes from the internal linear solve of the
    ``gravity`` reference load on the UNSTRESSED elastic stiffness ``K``
    (the classic linearized treatment: the base state pre-stresses the
    geometry, it does not re-stiffen the reference solve).  ``lambda``
    is therefore the critical multiplier ON THE ``gravity`` LOADS GIVEN
    the base state; because Kg is linear in N, for a shared distribution
    the exact identity ``lambda_base + lambda = lambda_no_base`` holds
    (pinned in the tests).  A base state at/beyond buckling makes
    ``K + Kg(N_base)`` indefinite — the Cholesky fails and an explicit
    warning is returned instead of factors.  Members named in ``base_N``
    that the frame assembly skipped are ignored; ``base_label`` is echoed
    as ``BucklingResult.base_case``.
    """
    warn: List[str] = []
    if model.shells:
        warn.append(f"{len(model.shells)} shell region(s) skipped "
                    "(frame-only buckling)")
    if model.links:
        warn.append(f"{len(model.links)} link element(s) skipped")
    if any(m.release_tokens() for m in model.members):
        warn.append("member end releases ignored (members treated continuous)")

    # ---- assemble elastic K and cache per-member transforms/props ----
    K, tag_of, coords, ele, asm_warn = assemble_elastic_stiffness(model)
    warn.extend(asm_warn)
    if not coords:
        return BucklingResult([], {}, dict(gravity), {}, warn +
                              ["no frame members to analyse"],
                              base_case=base_label)
    restr = _restraints(model, tag_of, coords)
    tags_sorted = sorted(coords)
    idx = {t: i for i, t in enumerate(tags_sorted)}
    ndof = 6 * len(coords)

    # ---- reference gravity nodal load vector ----
    F = _gravity_nodal_vector(model, gravity, tag_of, idx, ndof, warn)

    # ---- free-dof mask ----
    free = np.ones(ndof, dtype=bool)
    for t, r in restr.items():
        base = 6 * idx[t]
        for k in range(6):
            if r[k]:
                free[base + k] = False
    free_i = np.where(free)[0]
    if free_i.size == 0:
        return BucklingResult([], {}, dict(gravity), coords,
                              warn + ["all DOFs restrained"],
                              base_case=base_label)

    Kff = K[np.ix_(free_i, free_i)]
    # ---- linear static solve for member axial forces ----
    u = np.zeros(ndof)
    try:
        u[free_i] = np.linalg.solve(Kff, F[free_i])
    except np.linalg.LinAlgError:
        return BucklingResult([], {}, dict(gravity), coords,
                              warn + ["singular stiffness in the static "
                                      "solve; cannot form geometric stiffness"],
                              base_case=base_label)

    # ---- assemble geometric stiffness Kg from member axial forces ----
    # (plus, v0.25, the FIXED base geometric stiffness from base_N)
    Kg = np.zeros((ndof, ndof))
    Kg_base = np.zeros((ndof, ndof)) if base_N else None
    for (m, T, dofs, ea_over_L, L) in ele:
        ul = T @ u[dofs]
        N = ea_over_L * (ul[6] - ul[0])          # tension positive
        kgg = T.T @ _local_geometric(N, L) @ T
        Kg[np.ix_(dofs, dofs)] += kgg
        if Kg_base is not None and m.uid in base_N:
            Nb = float(base_N[m.uid])            # tension positive
            kgb = T.T @ _local_geometric(Nb, L) @ T
            Kg_base[np.ix_(dofs, dofs)] += kgb

    Kgff = Kg[np.ix_(free_i, free_i)]
    M = -Kgff                                    # K phi = lambda (-Kg) phi
    K_eff = Kff
    if Kg_base is not None:
        # two-load-set form: the base state pre-stresses the structure
        K_eff = Kff + Kg_base[np.ix_(free_i, free_i)]

    factors, vectors = _solve_buckling(K_eff, M)
    if not factors:
        if Kg_base is not None and not _is_spd(K_eff):
            warn.append("base axial state is at or beyond the buckling "
                        "load of the structure (K + Kg(N_base) is not "
                        "positive definite); no factors")
        else:
            warn.append("no positive buckling factors found "
                        "(gravity may induce no compression)")

    n_keep = min(num_modes, len(factors))
    modes: Dict[int, Dict[int, List[float]]] = {}
    for mno in range(n_keep):
        full = np.zeros(ndof)
        full[free_i] = vectors[mno]
        shape: Dict[int, List[float]] = {}
        for t in tags_sorted:
            b = 6 * idx[t]
            shape[t] = [float(v) for v in full[b:b + 6]]
        modes[mno + 1] = shape

    return BucklingResult([float(f) for f in factors[:n_keep]], modes,
                          dict(gravity), coords, warn,
                          base_case=base_label)


def _is_spd(A: np.ndarray) -> bool:
    """True when ``A`` admits a Cholesky factorization (SPD)."""
    try:
        np.linalg.cholesky(A)
        return True
    except np.linalg.LinAlgError:
        return False


def _gravity_nodal_vector(model: BuildingModel, gravity: Dict[str, float],
                          tag_of: Dict[Vec3, int], idx: Dict[int, int],
                          ndof: int, warn: List[str]) -> np.ndarray:
    """Equivalent global nodal load vector for the reference gravity."""
    F = np.zeros(ndof)

    def add(tag: Optional[int], fx: float, fy: float, fz: float) -> None:
        if tag is None:
            return
        b = 6 * idx[tag]
        F[b + 0] += fx
        F[b + 1] += fy
        F[b + 2] += fz

    members = {m.uid: m for m in model.members}
    flags = {"area": False, "story": False, "thermal": False}
    for pname, fac in gravity.items():
        pat = model.patterns.get(pname)
        if pat is None:
            continue
        for nl in pat.nodal_loads:
            add(tag_of.get(_pkey(nl.point)), fac * nl.fx, fac * nl.fy,
                fac * nl.fz)
        for ml in pat.all_member_loads():
            m = members.get(ml.member_uid)
            if m is None:
                continue
            g = _load_global_components(ml)
            if g is None:
                continue
            gx, gy, gz = g
            L = m.length
            if ml.kind == "point":
                P = ml.w
                wi, wj = P * (1.0 - ml.a), P * ml.a
            elif ml.kind == "udl":
                tot = ml.w * (ml.b - ml.a) * L
                wi = wj = 0.5 * tot
            else:  # trapezoid
                tot = 0.5 * (ml.w + ml.w2) * (ml.b - ml.a) * L
                wi = wj = 0.5 * tot
            ti, tj = tag_of.get(_pkey(m.pi)), tag_of.get(_pkey(m.pj))
            add(ti, fac * gx * wi, fac * gy * wi, fac * gz * wi)
            add(tj, fac * gx * wj, fac * gy * wj, fac * gz * wj)
        swf = getattr(pat, "self_weight_factor", 0.0)
        if swf:
            for m in model.members:
                sec = model.sections.get(m.section)
                mat = model.materials.get(sec.material) if sec else None
                if sec is None or mat is None:
                    continue
                w = swf * sec.A * mat.unit_weight * m.length
                ti, tj = tag_of.get(_pkey(m.pi)), tag_of.get(_pkey(m.pj))
                add(ti, 0.0, 0.0, -fac * w / 2.0)
                add(tj, 0.0, 0.0, -fac * w / 2.0)
        if pat.area_loads:
            flags["area"] = True
        if pat.story_forces:
            flags["story"] = True
        if pat.thermal_loads:
            flags["thermal"] = True
    if flags["area"]:
        warn.append("area (shell) loads ignored in buckling gravity")
    if flags["story"]:
        warn.append("story forces ignored in buckling gravity")
    if flags["thermal"]:
        warn.append("thermal loads ignored in buckling gravity")
    return F


def _solve_buckling(Kff: np.ndarray, M: np.ndarray
                    ) -> Tuple[List[float], List[np.ndarray]]:
    """Generalized eigenproblem ``Kff phi = lambda M phi`` (M = -Kg), numpy.

    ``Kff`` is SPD.  Cholesky ``Kff = L L^T`` reduces the pair to the
    standard SYMMETRIC eigenproblem ``C psi = mu psi`` with
    ``C = L^-1 M L^-T`` and ``mu = 1/lambda``; the largest positive ``mu``
    give the smallest positive ``lambda`` (the critical multipliers).  No
    scipy dependency: ``numpy.linalg.cholesky`` + ``numpy.linalg.eigh``.
    """
    try:
        L = np.linalg.cholesky(Kff)
    except np.linalg.LinAlgError:
        return [], []
    Y = np.linalg.solve(L, M)             # L^-1 M
    C = np.linalg.solve(L, Y.T)           # L^-1 M L^-T  (M symmetric)
    C = 0.5 * (C + C.T)
    mu, V = np.linalg.eigh(C)             # ascending mu, orthonormal V
    pairs: List[Tuple[float, np.ndarray]] = []
    for k in range(len(mu)):
        if mu[k] > 1e-9:
            lam = 1.0 / mu[k]
            psi = V[:, k]
            phi = np.linalg.solve(L.T, psi)   # L^-T psi
            pairs.append((lam, phi))
    pairs.sort(key=lambda p: p[0])
    factors = [p[0] for p in pairs]
    vectors = [p[1] for p in pairs]
    return factors, vectors
