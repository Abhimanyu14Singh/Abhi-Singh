"""Linear buckling on the ENGINE's finite-element model (frames, shells,
links, releases, diaphragms) — ``BucklingCase.formulation == "fem"``.

CONTRACT "Buckling with shells and links, sparse eigen and BucklingFEM".

The legacy v0.10 solver (:mod:`skyframe.core.buckling`, still the default
``formulation == "frame"``, byte-identical) assembles a dense numpy
stiffness of the bare frame only.  This module instead uses the engine's
own elastic OpenSees domain for ``K`` — every element the engine builds
(elasticBeamColumn / Timoshenko frames with offsets, per-DOF releases and
partial fixity, auto-mesh sub-elements, panel zones; ShellMITC4 / DKGQ /
ASD shells; elastic links; point / line / area springs; rigid and named
diaphragms) — and factors it ONCE (the factor-once sparse analysis of the
solver policy on large models):

1. reference state: the case's ``gravity`` patterns (every load type the
   engine applies: nodal, member span, self weight, AREA loads, thermal
   ...) are solved linearly on the elastic domain;
2. geometric stiffness ``Kg`` from the element responses of that state
   (:mod:`skyframe.engine.geom_stiffness`: frame axial forces with native
   release condensation, truss string stiffness, shell membrane stress
   resultants at the Gauss points);
3. ``K phi = lambda (-Kg) phi`` by shift-invert Lanczos with locking
   (:mod:`skyframe.engine.lanczos`) — each Lanczos step is ONE linear
   solve on the factored domain.

``base_case`` (two-load-set form ``(K + Kg_base) phi = lambda (-Kg) phi``):
the base static case is solved by the engine with its own settings; its
member axial forces (mean station N, as the frame formulation) and shell
membrane resultants (Gauss averages) form ``Kg_base``.  ``K + Kg_base`` is
never factored: a Davidson iteration preconditioned by the factored ``K``
(correction ``K^-1 r`` of each residual ``r = (K + Kg_b) x - lambda G x``,
Rayleigh-Ritz on the growing K-orthonormal basis) solves the pencil.

``include_shells`` / ``include_links`` (default True) — False analyses a
copy of the model without shell regions (and their area loads) / links;
the mode shapes are then reported on the full model's mesh node tags
(matched by coordinates) so the 3D view can draw them.

Modelling notes (documented):
* rigid end-zone arms, edge-tie chains and links carry no geometric
  stiffness (their stiffness is in K);
* eccentric (insertion-point) frame elements use their node-to-node chord
  for ``Kg``;
* nonlinear members (tension/compression-only, gap/hook links,
  compression-only springs) enter with their initial linear stiffness
  (warned).
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core.buckling import BucklingResult
from skyframe.engine import geom_stiffness as _gk
from skyframe.engine import lanczos as _lz

FORMULATIONS = ("frame", "fem")
#: relative residual tolerance of the buckling eigenpairs
TOL = 1e-9
#: Davidson iterations at most (base_case two-load-set solve)
MAX_DAVIDSON = 40


def is_fem(bc) -> bool:
    return getattr(bc, "formulation", "frame") == "fem"


# ----------------------------------------------------------- model copy
def analysis_model(model, bc):
    """The model the FEM buckling solve runs on (shells / links dropped
    when the case excludes them) and the list of exclusion notes."""
    inc_sh = bool(getattr(bc, "include_shells", True))
    inc_lk = bool(getattr(bc, "include_links", True))
    notes: List[str] = []
    if inc_sh and inc_lk:
        return model, notes
    m2 = copy.deepcopy(model)
    if not inc_sh and m2.shells:
        gone = {r.uid for r in m2.shells}
        notes.append(f"{len(gone)} shell region(s) excluded "
                     "(include_shells = false)")
        m2.shells = []
        for pat in m2.patterns.values():
            pat.area_loads = [a for a in pat.area_loads
                              if a.region_uid not in gone]
    if not inc_lk and m2.links:
        notes.append(f"{len(m2.links)} link element(s) excluded "
                     "(include_links = false)")
        m2.links = []
    # supports / nodal loads left on points no remaining element uses
    from skyframe.core.mesh import mesh_model
    pts = {(round(p[0], 6), round(p[1], 6), round(p[2], 6))
           for p in mesh_model(m2).points}

    def on(p):
        return (round(p[0], 6), round(p[1], 6), round(p[2], 6)) in pts
    n0 = len(m2.supports)
    m2.supports = [s for s in m2.supports if on(s.point)]
    if len(m2.supports) < n0:
        notes.append(f"{n0 - len(m2.supports)} support(s) on excluded "
                     "elements only were dropped")
    for pat in m2.patterns.values():
        pat.nodal_loads = [nl for nl in pat.nodal_loads if on(nl.point)]
    return m2, notes


# ----------------------------------------------------- geometric stiffness
def _member_axes(m, pi, pj):
    from skyframe.core.frame_axes import member_local_axes
    x, y, _z, _v, _vert = member_local_axes(m)
    xe = np.asarray(pj, float) - np.asarray(pi, float)
    xe = xe / np.linalg.norm(xe)
    y = np.asarray(y, float)
    y = y - xe * float(y @ xe)
    ny = np.linalg.norm(y)
    if ny < 1e-9:                                   # degenerate: any normal
        y = np.cross(xe, [0.0, 0.0, 1.0])
        if np.linalg.norm(y) < 1e-9:
            y = np.cross(xe, [1.0, 0.0, 0.0])
        ny = np.linalg.norm(y)
    y = y / ny
    z = np.cross(xe, y)
    return xe, y, z


def assemble_kg(eng, asm, space: _gk.Space,
                base_N: Optional[Dict[str, float]] = None,
                base_shell: Optional[Dict[int, List[float]]] = None,
                stats: Optional[dict] = None) -> _gk.SymOp:
    """Kg of the CURRENT domain state (``base_*`` None) or of a stored
    base state (member uid -> N, quad index -> 8 averaged resultants)."""
    from skyframe.engine import releases_eng as _rle
    from skyframe.engine import shell_elements as _shel
    model = eng.model
    K = _gk.SymOp(space.n)
    members = {m.uid: m for m in model.members}
    nf = ns = 0
    base_mode = base_N is not None or base_shell is not None
    segs_of = asm.mesh.segments if asm.mesh is not None else {}
    for (uid, si), etag in asm.seg_ele.items():
        m = members.get(uid)
        if m is None:
            m = (getattr(model, "_np_parents", None) or {}).get(uid)
        nodes = list(ops.eleNodes(etag))
        if len(nodes) != 2:
            continue
        pi, pj = ops.nodeCoord(nodes[0]), ops.nodeCoord(nodes[1])
        if uid in asm.truss_uids:
            if base_mode:
                N = float((base_N or {}).get(uid, 0.0))
            else:
                r = ops.eleResponse(etag, "axialForce")
                N = float(r[0]) if r else 0.0
            if N == 0.0:
                continue
            K.add(space.dofs(nodes), _gk.truss_kg_global(N, pi, pj))
            nf += 1
            continue
        if base_mode:
            if base_N is None or uid not in base_N:
                continue
            N = float(base_N[uid])
        else:
            lf = ops.eleResponse(etag, "localForce")
            if not lf or len(lf) < 12:
                continue
            N = 0.5 * (-float(lf[0]) + float(lf[6]))
        if N == 0.0 or m is None:
            continue
        segs = segs_of.get(uid) or [None]
        plan = _rle.plan(m, uid in asm.timo)
        cz, cy = _rle.native_codes(plan, si, len(segs) - 1)
        axes = _member_axes(m, pi, pj)
        K.add(space.dofs(nodes), _gk.frame_kg_global(N, pi, pj, axes, cz, cy))
        nf += 1
    regions = {r.uid: r for r in model.shells}
    for qi, etag in enumerate(asm.quad_ele):
        q = asm.shell_quads[qi]
        reg = regions.get(q["region"])
        if reg is None:
            continue
        ssec = model.shell_sections.get(reg.section)
        name = (_shel.element_name(ssec, len(q["nodes"])) if ssec is not None
                else ("ShellMITC4" if len(q["nodes"]) == 4 else "ShellDKGT"))
        if base_mode:
            vals = (base_shell or {}).get(qi)
            if not vals:
                continue
        else:
            ops.eleResponse(etag, "forces")        # stale-stress warm-up
            vals = ops.eleResponse(etag, "stresses")
            if not vals or len(vals) % 8:
                continue
        tens = _gk.membrane_tensors(name, vals)
        if not any(np.any(t != 0.0) for t in tens):
            continue
        P = np.array([ops.nodeCoord(t) for t in q["nodes"]], dtype=float)
        K.add(space.dofs(q["nodes"]), _gk.shell_kg_global(name, P, tens))
        ns += 1
    if stats is not None:
        stats["frame_elements"] = stats.get("frame_elements", 0) + nf
        stats["shell_elements"] = stats.get("shell_elements", 0) + ns
    return K


# ------------------------------------------------------------- driver
def run(eng, name: str) -> BucklingResult:
    """Solve one ``formulation == "fem"`` buckling case of ``eng.model``."""
    from skyframe.engine.opensees_engine import (OpenSeesEngine, _REUSE,
                                                 _quiet_native_output)
    model = eng.model
    bc = model.buckling_cases[name]
    m2, warn = analysis_model(model, bc)
    fe = eng if m2 is model else OpenSeesEngine(m2)
    nev = int(bc.num_modes)
    stats: dict = {}
    # ---- base (stressed) state: engine static solve with its settings
    base_N = base_shell = None
    base_case = getattr(bc, "base_case", None)
    if base_case is not None:
        if base_case not in m2.cases:
            raise ValueError(f"Buckling case {name!r}: base_case "
                             f"{base_case!r} is not a static load case")
        br = fe.run_static(base_case)
        base_N = {}
        for uid, st in br.member_stations.items():
            vals = st.get("N") or []
            if vals:
                base_N[uid] = float(sum(vals) / len(vals))
        base_shell = {int(k): list(v) for k, v in
                      (br.shell_forces or {}).items()}
    # ---- reference state on the elastic domain
    with _quiet_native_output():
        asm = fe._elastic_domain()
    space = _gk.Space(asm.node_coords)
    fe._seg_span_loads = {}
    fe._seg_fef = {}
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    _REUSE["loaded"] = True
    for pname, fac in bc.gravity.items():
        fe._apply_pattern(asm, pname, float(fac))
    fe._setup_linear_reused(asm)
    from skyframe.engine import solver_policy as _slv0
    if _slv0.analyze_reused(fe, asm) != 0:
        raise RuntimeError(f"Buckling case {name!r}: the reference static "
                           "solve failed (unstable structure?)")
    Kg = assemble_kg(fe, asm, space, stats=stats)
    Kgb = (assemble_kg(fe, asm, space, base_N=base_N or {},
                       base_shell=base_shell or {})
           if base_case is not None else None)
    if (fe._axial_only_present() or fe._nonlinear_static_links_present()
            or fe._compression_only_springs_present()):
        warn.append("nonlinear members / links / springs enter with their "
                    "initial linear stiffness")
    from skyframe.engine import solver_policy as _slv
    from skyframe.engine import sparse_solve as _sps
    solver = _sps.EngineSolver(fe, space)
    free = _sps.free_mask(asm, space)

    def gmul(x):
        return -Kg.matvec(x)

    factors: List[float] = []
    vectors: List[np.ndarray] = []
    conv = True
    resid: List[float] = []
    if Kg.nnz() == 0:
        warn.append("no positive buckling factors found "
                    "(gravity may induce no compression)")
    else:
        lr = _lz.eigs(solver, gmul, free, nev, tol=TOL)
        conv = lr.converged
        if Kgb is None:
            factors = [1.0 / t for t in lr.thetas]
            vectors = list(lr.vectors)
            resid = list(lr.residuals)
        else:
            factors, vectors, conv, resid = _two_load_set(
                solver, gmul, Kgb, lr, nev)
            if not factors and lr.thetas:
                warn.append("base axial state is at or beyond the buckling "
                            "load of the structure (K + Kg(N_base) is not "
                            "positive definite); no factors")
        if not factors and not any("no factors" in w for w in warn):
            warn.append("no positive buckling factors found "
                        "(gravity may induce no compression)")
        if not conv:
            warn.append("Lanczos did not reach the residual tolerance "
                        f"{TOL:g} (max residual "
                        f"{max(resid) if resid else float('nan'):.2e})")
    # ---- mode shapes (K-normalised, deterministic sign)
    tag_map = _main_tags(model, m2, asm)
    modes: Dict[int, Dict[int, List[float]]] = {}
    for k, x in enumerate(vectors[:nev]):
        X = x.reshape(-1, 6)
        imax = int(np.argmax(np.abs(x)))
        if x[imax] < 0.0:
            X = -X
        shape: Dict[int, List[float]] = {}
        for i, t in enumerate(space.tags):
            tt = tag_map.get(t, t) if tag_map is not None else t
            if tag_map is not None and t not in tag_map:
                continue
            shape[tt] = [float(v) for v in X[i]]
        modes[k + 1] = shape
    coords = ({tag_map[t]: c for t, c in asm.node_coords.items()
               if t in tag_map} if tag_map is not None
              else dict(asm.node_coords))
    res = BucklingResult([float(f) for f in factors[:nev]], modes,
                         dict(bc.gravity), coords, warn,
                         base_case=base_case)
    res.extra = {
        "formulation": "fem",
        "include_shells": bool(getattr(bc, "include_shells", True)),
        "include_links": bool(getattr(bc, "include_links", True)),
        "solver": {"system": solver.system,
                   "equations_estimate": _slv.estimate_equations(asm),
                   "solves": int(solver.count),
                   "converged": bool(conv),
                   "max_residual": (float(max(resid)) if resid else 0.0)},
        "geometric_stiffness": {
            "frame_elements": int(stats.get("frame_elements", 0)),
            "shell_elements": int(stats.get("shell_elements", 0))},
    }
    return res


def _main_tags(model, m2, asm) -> Optional[Dict[int, int]]:
    """FE tag -> full-model mesh tag (coordinate match) when the solve ran
    on a reduced copy; None when the tags are the model's own."""
    if m2 is model:
        return None
    from skyframe.core import mesh_cache as _mc
    pts = _mc.cached_mesh(model).points
    key = {(round(p[0], 6), round(p[1], 6), round(p[2], 6)): i + 1
           for i, p in enumerate(pts)}
    out: Dict[int, int] = {}
    seen = set()
    for t in sorted(asm.node_coords):
        c = asm.node_coords[t]
        k = (round(c[0], 6), round(c[1], 6), round(c[2], 6))
        mt = key.get(k)
        if mt is not None and mt not in seen:
            out[t] = mt
            seen.add(mt)
    return out


def _two_load_set(solver, gmul, Kgb: _gk.SymOp, lr, nev: int
                  ) -> Tuple[List[float], List[np.ndarray], bool,
                             List[float]]:
    """``(K + Kg_b) x = lambda G x`` by K-preconditioned Davidson."""
    Zx, Zf = _lz._korth(list(lr.vectors), list(lr.loads))
    lam: List[float] = []
    X: List[np.ndarray] = []
    res: List[float] = []
    for _it in range(MAX_DAVIDSON):
        if not Zx:
            return [], [], False, []
        Z = np.array(Zx)
        A = np.eye(len(Zx)) + Z @ np.array([Kgb.matvec(z) for z in Zx]).T
        A = 0.5 * (A + A.T)
        B = Z @ np.array([gmul(z) for z in Zx]).T
        B = 0.5 * (B + B.T)
        try:
            L = np.linalg.cholesky(A)
        except np.linalg.LinAlgError:
            return [], [], False, []
        Li = np.linalg.inv(L)
        C = Li @ B @ Li.T
        mu, V = np.linalg.eigh(0.5 * (C + C.T))
        order = [i for i in np.argsort(-mu) if mu[i] > 1e-12][:nev]
        if not order:
            return [], [], True, []
        W = Li.T @ V[:, order]
        lam = [1.0 / float(mu[i]) for i in order]
        X = [W[:, k] @ Z for k in range(len(order))]
        Fx = [W[:, k] @ np.array(Zf) for k in range(len(order))]
        res = []
        corr_x, corr_f = [], []
        scale = max(abs(float(mu[order[0]])), 1e-300)
        for l_k, x, f in zip(lam, X, Fx):
            r = f + Kgb.matvec(x) - l_k * gmul(x)
            nx = math.sqrt(max(float(x @ f), 1e-300))
            c = solver(r)
            rn = math.sqrt(max(float(c @ r), 0.0)) / (nx * max(l_k, 1e-300))
            res.append(rn)
            if rn > TOL:
                corr_x.append(c)
                corr_f.append(r)
        if not corr_x:
            return lam, X, True, res
        Zx, Zf = _lz._korth(Zx + corr_x, Zf + corr_f)
        _ = scale
    return lam, X, False, res
