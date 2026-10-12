"""Sparse stability (mechanism) check for models above the dense cap.

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

The dense check of :func:`skyframe.core.checks.stability_diagnostics`
forms the full stiffness (``FullGeneral`` + ``printA``) and is capped at
``STABILITY_MAX_DOFS`` (3 000) equations.  Above the cap this module runs
instead (``method="auto"``, the default; ``"sparse"`` forces it,
``"dense"`` keeps the legacy "too large" skip):

1. the engine's elastic domain is built as for the dense check, and every
   node gets a soft grounded spring on each FREE dof (``zeroLength`` to a
   fixed twin node) of stiffness ``ks = EPS x k_ref`` (translations
   ``k_ref`` = median EA/L of the frame elements and E t of the shells;
   rotations ``k_ref lc^2``, ``lc`` the median member length — the same
   rotation scaling the dense check reports with);
2. ``K + S`` is factored once (MUMPS factor reuse) and the generalized
   problem ``K phi = mu S phi`` is solved by shift-invert Lanczos for the
   largest ``theta = 1 / (1 + mu)`` (:mod:`skyframe.engine.lanczos`):
   a mechanism (``phi^T K phi`` below ``EPS`` of the reference stiffness)
   has ``mu < 1``, i.e. ``theta > 1/2``; an exact mechanism has
   ``theta = 1``; every stable mode has ``theta ~ EPS / (relative
   stiffness)``;
3. mechanism shapes are the Lanczos vectors (physical joint
   displacements, diaphragm masters left out of the report exactly as in
   the dense check) and are reported in the dense check's format
   (``mechanisms``, ``unstable_dofs``, ``STABILITY_MECHANISM`` issues).

``lowest_eigenvalues`` are then ``EPS * mu`` (stiffness relative to the
reference stiffness, ascending); the condition number and diagonal-ratio
fields stay ``None`` (no factorization pivots are exposed) — the result
carries ``"method": "sparse"`` and ``"sparse": {...}`` solver details.
"""

from __future__ import annotations

import math
import time
from typing import List

import numpy as np

#: spring stiffness relative to the reference stiffness (mechanism
#: threshold: Rayleigh stiffness below EPS x k_ref)
EPS = 1e-10
#: largest equation count the sparse check takes
SPARSE_MAX_DOFS = 400000
METHODS = ("auto", "dense", "sparse")


def use_sparse(method: str, est: int, max_dofs: int) -> bool:
    from skyframe.core.checks import STABILITY_MAX_DOFS
    if method == "sparse":
        return est <= SPARSE_MAX_DOFS
    if method == "dense":
        return False
    return est > max(int(max_dofs), STABILITY_MAX_DOFS) \
        and est <= SPARSE_MAX_DOFS


def _ref_stiffness(model, asm) -> tuple:
    vals: List[float] = []
    lens: List[float] = []
    for m in model.members:
        sec = model.sections.get(m.section)
        mat = model.materials.get(sec.material) if sec else None
        if sec is None or mat is None or m.length <= 0:
            continue
        vals.append(mat.E * sec.A / m.length)
        lens.append(m.length)
    for r in model.shells:
        ss = model.shell_sections.get(r.section)
        mat = model.materials.get(ss.material) if ss else None
        if ss is None or mat is None:
            continue
        vals.append(mat.E * ss.total_thickness)
        lens.append(float(r.mesh_size))
    kt = float(np.median(vals)) if vals else 1.0
    lc = float(np.median(lens)) if lens else 1.0
    return kt, kt * lc * lc, lc


def run(model, eng, asm, result: dict, est: int, n_report: int,
        t0: float) -> dict:
    import openseespy.opensees as ops
    from skyframe.core import checks as _ck
    from skyframe.engine import geom_stiffness as _gk
    from skyframe.engine import lanczos as _lz
    from skyframe.engine.sparse_solve import StandaloneSolver
    issues = result["issues"]
    result["method"] = "sparse"
    kt, kr, lc = _ref_stiffness(model, asm)
    space = _gk.Space(sorted(asm.node_coords))
    svec = np.zeros(space.n)
    none = (0,) * 6
    tag0 = max(int(t) for t in ops.getNodeTags()) + 1
    ele0 = max([int(e) for e in (ops.getEleTags() or [0])]) + 1
    mt, mr = 990001, 990002
    ops.uniaxialMaterial("Elastic", mt, EPS * kt)
    ops.uniaxialMaterial("Elastic", mr, EPS * kr)
    nt = ne = 0
    for i, t in enumerate(space.tags):
        r = asm.node_restraints.get(t, none)
        dofs = [d for d in range(6) if not r[d]]
        if not dofs:
            continue
        g = tag0 + nt
        nt += 1
        ops.node(g, *ops.nodeCoord(t))
        ops.fix(g, 1, 1, 1, 1, 1, 1)
        mats = [mt if d < 3 else mr for d in dofs]
        ops.element("zeroLength", ele0 + ne, g, t, "-mat", *mats,
                    "-dir", *[d + 1 for d in dofs])
        ne += 1
        for d in dofs:
            svec[6 * i + d] = EPS * (kt if d < 3 else kr)
    free = svec > 0.0
    solver = StandaloneSolver(space, asm.use_transformation)
    nev = min(_ck.MAX_MODES_REPORTED + 1, max(int(np.count_nonzero(free)),
                                               1))

    def gmul(x):
        return svec * x
    try:
        lr = _lz.eigs(solver, gmul, free, nev, tol=1e-8, max_passes=2)
    except RuntimeError as exc:
        ops.wipe()
        issues.append(_ck._issue("error", "STABILITY_BUILD_FAILED",
                                 f"Sparse stability solve failed: {exc}",
                                 []))
        result["message"] = issues[-1]["message"]
        return result
    ops.wipe()
    result["n_equations"] = int(est)
    mus = [max(1.0 / th - 1.0, 0.0) for th in lr.thetas]
    result["lowest_eigenvalues"] = [EPS * m for m in sorted(mus)[:6]]
    mech = [k for k, th in enumerate(lr.thetas) if th > 0.5]
    result["sparse"] = {"system": solver.system, "solves": solver.count,
                        "converged": bool(lr.converged),
                        "spring_ratio": EPS,
                        "reference_stiffness": {"translation": kt,
                                                "rotation": kr,
                                                "length": lc},
                        "ground_springs": ne}
    masters_rev = {t: s for s, t in asm.masters.items()}
    objs = _ck._node_objects(model, asm)
    phys_keys = [(t, d) for t in space.tags for d in range(6)]
    report_mask = np.array([t not in masters_rev for t, _d in phys_keys])
    rot_scale = np.array([1.0 if d < 3 else lc for _t, d in phys_keys])
    if mech:
        P = np.column_stack([lr.vectors[k] * rot_scale for k in mech])
        P[~report_mask, :] = 0.0
        keep = np.linalg.norm(P, axis=0) > 0
        Q = np.linalg.qr(P[:, keep])[0] if keep.any() else P[:, :0]
        part = (Q * Q).sum(axis=1)
        order = _ck._rank(part, phys_keys)
        top = [i for i in order[:max(n_report * 3, 24)] if part[i] > 1e-6]
        pmax = part[top[0]] if top else 0.0
        result["unstable_dofs"] = [
            _ck._location_entry(phys_keys[i][0], phys_keys[i][1], part[i],
                                asm, objs, masters_rev)
            for i in top if part[i] >= 0.05 * pmax][:max(n_report * 3, 24)]
        for k in range(min(Q.shape[1], _ck.MAX_MODES_REPORTED)):
            q = Q[:, k] ** 2
            o = _ck._rank(q, phys_keys)
            qmax = q[o[0]] if q.size else 0.0
            dofs = [_ck._location_entry(phys_keys[i][0], phys_keys[i][1],
                                        q[i], asm, objs, masters_rev)
                    for i in o[:n_report] if q[i] >= 0.05 * qmax]
            result["mechanisms"].append({
                "mode": k + 1,
                "scaled_eigenvalue": EPS * mus[mech[k]],
                "dofs": dofs})
            d0 = dofs[0] if dofs else None
            where = (f" at joint {_ck._fmt(d0['point'])} DOF {d0['dof']}"
                     + (f" ({', '.join(d0['objects'][:4])})"
                        if d0["objects"] else "")) if d0 else ""
            more = "+" if len(mech) >= nev else ""
            issues.append(_ck._issue(
                "error", "STABILITY_MECHANISM",
                f"Structure is unstable (mechanism {k + 1} of "
                f"{len(mech)}{more}){where}",
                d0["objects"] if d0 else [], d0["point"] if d0 else None))
    result["n_mechanisms"] = len(mech)
    result["stable"] = not mech
    if mech:
        top = result["unstable_dofs"][0] if result["unstable_dofs"] else None
        result["message"] = (
            f"Structure is UNSTABLE: {len(mech)} mechanism(s)"
            + (f"; largest motion at joint {_ck._fmt(top['point'])} DOF "
               f"{top['dof']}" if top else "") + " (sparse check)")
    else:
        result["message"] = "Structure is stable (sparse check)"
    result["elapsed_s"] = round(time.perf_counter() - t0, 4)
    return result
