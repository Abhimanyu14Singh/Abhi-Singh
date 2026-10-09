"""Nonlinear Static load cases and case chaining — engine side.

See :mod:`skyframe.core.nonlinear_static` for the case definition and
CONTRACT "Nonlinear static load cases and case chaining".

Every analysis here solves a CHAIN of nonlinear static cases
``[root, ..., target]`` in ONE OpenSees domain (no wipe between stages):

1. the domain is built ONCE with the target's hinge definition (the
   pushover hinge-insertion machinery, ``_build(hinge_case=...)``) and the
   chain's geometric transformation (none -> Linear, p_delta -> PDelta,
   large_displacement -> Corotational);
2. each stage k adds its loads in its own Plain pattern (tag 100 + k, on a
   Linear time series) and is solved with Newton (NormDispIncr 1e-8, 50;
   NewtonLineSearch retry, then a 10x substep retry) by LoadControl
   (1/steps) or DisplacementControl on the monitored DOF; the stage is
   then held with ``loadConst -time 0`` and the next stage continues from
   its END STATE.

Consumers: :func:`run_case` (the case's own final-state results + per-step
history), :func:`run_start_state` (pushover ``start_from`` a nonlinear
static case) and :func:`run_modal_from_case` (MODAL eigen solve on the
tangent stiffness at the end of a nonlinear static case).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import openseespy.opensees as ops

from skyframe.core.model import LoadCase
from skyframe.core.nonlinear_static import NLS_DOFS, chain_of

_TOL = 1e-6
_PAT_BASE = 100
_SUBSTEPS = 10
_TRANSF = {"none": None, "p_delta": "PDelta",
           "large_displacement": "Corotational"}


@dataclass
class NonlinearStaticResults:
    """Results of one nonlinear static case.

    ``case``: the FINAL (cumulative, including the ``start_from`` chain)
    state in the static-case shape.  ``history``: per converged step of
    THIS case (entry 0 = its initial state).  Hinge outputs mirror the
    pushover ones."""

    name: str
    case: object                               # CaseResults
    history: dict
    chain: List[str]
    load_application: str
    geometric: str
    converged: bool = True
    final_load_factor: float = 1.0
    hinge_rotations: Dict[str, float] = field(default_factory=dict)
    yielded: List[str] = field(default_factory=list)
    hinge_detail: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.case.to_dict()
        d.pop("warning", None)
        d["nonlinear"] = {
            "chain": list(self.chain),
            "load_application": self.load_application,
            "geometric": self.geometric,
            "converged": bool(self.converged),
            "final_load_factor": float(self.final_load_factor),
            "history": {k: (dict(v) if isinstance(v, dict) else
                            list(v) if isinstance(v, list) else v)
                        for k, v in self.history.items()},
            "hinge_rotations": {u: float(r) for u, r in
                                self.hinge_rotations.items()},
            "yielded": list(self.yielded),
            "hinges": [dict(h) for h in self.hinge_detail],
            "warnings": list(self.warnings),
        }
        d["nonlinear"]["history"]["base"] = {
            k: list(v) for k, v in self.history["base"].items()}
        return d


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cache(eng) -> dict:
    c = getattr(eng, "_nls_cache", None)
    if c is None:
        c = eng._nls_cache = {}
    return c


def _setup(asm, *integrator) -> None:
    ops.wipeAnalysis()
    ops.constraints("Transformation" if asm.use_transformation
                    else "Plain")
    ops.numberer("RCM")
    ops.system("BandGeneral")
    ops.test("NormDispIncr", 1.0e-8, 50)
    ops.algorithm("Newton")
    ops.integrator(*integrator)
    ops.analysis("Static")


def _try_step() -> bool:
    ok = ops.analyze(1)
    if ok != 0:
        ops.algorithm("NewtonLineSearch")
        ok = ops.analyze(1)
        ops.algorithm("Newton")
    return ok == 0


def _step(asm, integ: Tuple) -> bool:
    """One increment ``integ`` (integrator args) with retries; the
    analysis is left set up with ``integ``."""
    if _try_step():
        return True
    sub = list(integ)
    sub[-1] = integ[-1] / _SUBSTEPS
    _setup(asm, *sub)
    ok = all(_try_step() for _ in range(_SUBSTEPS))
    _setup(asm, *integ)
    return ok


def _monitor(eng, asm, case) -> Tuple[int, int]:
    """(monitored node tag, 1-based dof) of a case."""
    model = eng.model
    dof = NLS_DOFS.index(case.control_dof) + 1
    if case.control_point is not None:
        return eng._find_node(asm, tuple(case.control_point)), dof
    if case.control_story is not None:
        s = case.control_story
        if s in asm.masters:
            return asm.masters[s], dof
        nodes = asm.story_nodes.get(s) or []
        if not nodes:
            raise ValueError(f"Nonlinear static case {case.name!r}: control "
                             f"story {s!r} has no nodes")
        return min(nodes), dof
    if model.stories and model.stories[-1].name in asm.masters:
        return asm.masters[model.stories[-1].name], dof
    zmax = max(c[2] for c in asm.struct_coords.values())
    return min(t for t, c in asm.struct_coords.items()
               if abs(c[2] - zmax) < _TOL), dof


def _drive(asm, mon: int, dof: int) -> int:
    if dof in (1, 2, 6):
        from skyframe.engine.pushover_distribution import drive_node
        return drive_node(asm, mon)
    return mon


def _reactions(eng, asm, node_disp=None) -> Dict[int, List[float]]:
    """Support reactions (hinge duplicates of supports folded into their
    support node) + grounded-spring reactions."""
    ops.reactions()
    sup = set(asm.support_tags)
    reac = {t: list(ops.nodeReaction(t)) for t in asm.support_tags}
    for dup, orig in asm.hinge_dup_of.items():
        if orig in sup:
            r = ops.nodeReaction(dup)
            reac[orig] = [a + float(b) for a, b in zip(reac[orig], r)]
    if node_disp is None:
        tags = (set(asm.spring_nodes) | set(asm.ent_springs)
                | set(asm.spring_ground))
        node_disp = {t: list(ops.nodeDisp(t)) for t in tags}
    eng._add_spring_reactions(asm, node_disp, reac)
    return reac


def _scale_records(recs: Dict, fef: Dict, lam: float):
    out_r = {}
    for key, lst in recs.items():
        new = []
        for r in lst:
            if r[0] == "trap":
                new.append(("trap", tuple(v * lam for v in r[1]),
                            tuple(v * lam for v in r[2]), r[3], r[4]))
            else:
                new.append((r[0], tuple(v * lam for v in r[1]), r[2]))
        out_r[key] = new
    out_f = {key: v * lam for key, v in fef.items()}
    return out_r, out_f


class _HingeRecorder:
    """Per-step hinge bookkeeping (the pushover recording, verbatim
    conventions): peak |rotation| per uid, yielded uids (Steel01 springs:
    |rot| > My/k about either axis; asce41 / fiber: state != elastic),
    asce41 + fiber per-hinge histories."""

    def __init__(self, asm):
        self.asm = asm
        self.peak: Dict[str, float] = {}
        self.yielded: set = set()
        self.hist = {k: {"rot": [], "moment": [], "state": []}
                     for k in asm.hinge_backbone}
        self.fhist = {k: {"rot": [], "moment": [], "rot_plastic": [],
                          "state": []} for k in asm.fiber_hinges}
        from skyframe.engine.user_hinges import Recorder
        self.uh = Recorder(asm)                       # B10 user hinges

    def record(self) -> None:
        from skyframe.design.hinges import hinge_state as _hstate
        asm = self.asm
        for (uid, end), etag in asm.hinge_ele.items():
            defo = ops.eleResponse(etag, "deformation")
            ry = float(defo[1]) if len(defo) >= 3 else 0.0
            rz = float(defo[2]) if len(defo) >= 3 else 0.0
            rot = max(abs(ry), abs(rz))
            if rot > self.peak.get(uid, 0.0):
                self.peak[uid] = rot
            spec = asm.hinge_backbone.get((uid, end))
            if spec is not None:
                # spring (material) forces of dirs (4, 5, 6); "force" would be the
                # 12 GLOBAL nodal forces, whose index 2 is node-i Fz, not M3
                frc = ops.eleResponse(etag, "basicForce")
                m33 = float(frc[2]) if len(frc) >= 3 else 0.0
                st = _hstate(rz, spec["bb"], spec["k33"])
                hh = self.hist[(uid, end)]
                hh["rot"].append(rz)
                hh["moment"].append(m33)
                hh["state"].append(st)
                if st != "elastic":
                    self.yielded.add(uid)
            else:
                y = asm.hinge_rot_yield.get((uid, end))
                if y is not None and (abs(ry) > y[0] * (1 + 1e-9)
                                      or abs(rz) > y[1] * (1 + 1e-9)):
                    self.yielded.add(uid)
        for (uid, end), spec in asm.fiber_hinges.items():
            bd = ops.eleResponse(spec["ele"], "basicDeformation")
            bf = ops.eleResponse(spec["ele"], "basicForce")
            sd = ops.eleResponse(spec["ele"], "section", spec["sec"],
                                 "deformation")
            idx = 1 if end == "i" else 2
            rot = float(bd[idx]) if len(bd) > idx else 0.0
            mom = float(bf[idx]) if len(bf) > idx else 0.0
            kz = float(sd[1]) if len(sd) > 1 else 0.0
            t_pl = max(0.0, abs(kz) - spec["kappa_y"]) * spec["lp"]
            st = _hstate(t_pl, spec["bb"], math.inf)
            hh = self.fhist[(uid, end)]
            hh["rot"].append(rot)
            hh["moment"].append(mom)
            hh["rot_plastic"].append(t_pl)
            hh["state"].append(st)
            if st != "elastic":
                self.yielded.add(uid)
            if abs(rot) > self.peak.get(uid, 0.0):
                self.peak[uid] = abs(rot)
        self.uh.record(self.peak, self.yielded)

    def detail(self) -> List[dict]:
        out: List[dict] = []
        keys = ("My", "thy", "a", "b", "c", "IO", "LS", "CP", "kind")
        for (uid, end), spec in self.asm.hinge_backbone.items():
            bb = spec["bb"]
            out.append({"uid": uid, "end": end,
                        **{k: bb[k] for k in keys},
                        **{k: list(v) for k, v in
                           self.hist[(uid, end)].items()}})
        for (uid, end), spec in self.asm.fiber_hinges.items():
            bb = spec["bb"]
            out.append({"uid": uid, "end": end,
                        **{k: bb[k] for k in keys},
                        "fiber": True, "lp": spec["lp"],
                        "kappa_y": spec["kappa_y"],
                        **{k: list(v) for k, v in
                           self.fhist[(uid, end)].items()}})
        out.extend(self.uh.detail())
        return out


# --------------------------------------------------------------------------- #
# the chain solver
# --------------------------------------------------------------------------- #
def _chain_geometric(model, chain: List[str]) -> str:
    geo = {model.nonlinear_static_cases[n].geometric for n in chain}
    if len(geo) != 1:
        raise ValueError(f"nonlinear static chain {chain}: mixed geometric "
                         f"options {sorted(geo)}")
    return geo.pop()


def _build_for(eng, hinge_case, geometric: str):
    asm = eng._build(hinge_case=hinge_case, transf=_TRANSF[geometric])
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    return asm


class _ChainState:
    def __init__(self):
        self.k = 0
        self.eff: Dict[str, float] = {}          # pattern -> total factor
        self.recs: Dict = {}
        self.fef: Dict = {}


def _run_stage(eng, asm, st: _ChainState, case, on_step=None,
               tolerate_failure: bool = False) -> Tuple[float, bool, str]:
    """Apply + solve one chain stage; returns (final lambda, converged,
    warning)."""
    tag = _PAT_BASE + st.k
    st.k += 1
    ops.timeSeries("Linear", tag)
    ops.pattern("Plain", tag, tag)
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    for ld in case.loads:
        eng._apply_pattern(asm, ld["pattern"], float(ld["scale"]))
    s_recs, s_fef = eng._seg_span_loads, eng._seg_fef
    n = int(case.steps)
    if case.load_application == "displacement_control":
        mon, dof = _monitor(eng, asm, case)
        d_start = float(ops.nodeDisp(mon, dof))
        du = (float(case.target_disp) - d_start) / n
        integ = ("DisplacementControl", _drive(asm, mon, dof), dof, du)
    else:
        integ = ("LoadControl", 1.0 / n)
    _setup(asm, *integ)
    ok_all, warn = True, ""
    for i in range(n):
        if not _step(asm, integ):
            ok_all = False
            warn = (f"nonlinear static case {case.name!r} stopped early at "
                    f"step {i}/{n}: the solution did not converge (results "
                    "of the last converged step returned)")
            break
        if on_step is not None:
            on_step()
    if not ok_all and not tolerate_failure:
        raise RuntimeError(f"Nonlinear static case {case.name!r} (start "
                           "state of a chained analysis) failed to "
                           "converge")
    lam = float(ops.getLoadFactor(tag))
    ops.loadConst("-time", 0.0)
    r, f = _scale_records(s_recs, s_fef, lam)
    for key, lst in r.items():
        st.recs.setdefault(key, []).extend(lst)
    for key, v in f.items():
        st.fef[key] = st.fef[key] + v if key in st.fef else v
    for p, s in case.pattern_factors().items():
        st.eff[p] = st.eff.get(p, 0.0) + s * lam
    eng._seg_span_loads, eng._seg_fef = st.recs, st.fef
    return lam, ok_all, warn


def _run_prefix(eng, asm, names: List[str]) -> _ChainState:
    st = _ChainState()
    for n in names:
        _run_stage(eng, asm, st, eng.model.nonlinear_static_cases[n])
    return st


def run_start_state(eng, asm, name: str) -> None:
    """Solve the chain ending at nonlinear static case ``name`` inside the
    live domain ``asm`` (pushover ``start_from``)."""
    chain = chain_of(eng.model, name)
    _chain_geometric(eng.model, chain)
    _run_prefix(eng, asm, chain)
    eng._seg_span_loads = {}
    eng._seg_fef = {}


def run_case(eng, name: str) -> NonlinearStaticResults:
    """Run one nonlinear static case (cached per engine)."""
    cache = _cache(eng)
    if name in cache:
        return cache[name]
    model = eng.model
    cases = getattr(model, "nonlinear_static_cases", {}) or {}
    if name not in cases:
        raise ValueError(f"Unknown nonlinear static case {name!r}")
    case = cases[name]
    chain = chain_of(model, name)
    geo = _chain_geometric(model, chain)
    asm = _build_for(eng, case, geo)
    st = _run_prefix(eng, asm, chain[:-1])

    mon, dof = _monitor(eng, asm, case)
    rec = _HingeRecorder(asm)
    hist = {"step": [], "load_factor": [], "disp": [],
            "base": {k: [] for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}}
    tag = _PAT_BASE + st.k

    def sample(i: int, lam: float) -> None:
        hist["step"].append(i)
        hist["load_factor"].append(lam)
        hist["disp"].append(float(ops.nodeDisp(mon, dof)))
        base = eng._base_totals(asm, _reactions(eng, asm))
        for k, v in base.items():
            hist["base"][k].append(float(v))
        rec.record()

    sample(0, 0.0)
    counter = {"i": 0}

    def on_step() -> None:
        counter["i"] += 1
        sample(counter["i"], float(ops.getLoadFactor(tag)))

    lam, ok, warn = _run_stage(eng, asm, st, case, on_step=on_step,
                               tolerate_failure=True)
    hist["monitored"] = {"node": mon, "dof": NLS_DOFS[dof - 1],
                         "point": list(asm.node_coords[mon])}

    node_disp = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
    reactions = _reactions(eng, asm, node_disp)
    base = eng._base_totals(asm, reactions)
    mf, ms, md = eng._member_outputs(asm, node_disp=node_disp)
    story = eng._story_results(asm, LoadCase(name, dict(st.eff)), node_disp)
    shell_forces = eng._shell_outputs(asm)
    shell_nodal = eng._shell_nodal(asm) if eng._piers_enabled() else {}
    from skyframe.engine.opensees_engine import CaseResults
    cr = CaseResults(name, node_disp, reactions, base, mf, story, ms,
                     shell_forces=shell_forces, shell_nodal=shell_nodal,
                     member_deflections=md)
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    res = NonlinearStaticResults(
        name, cr, hist, chain, case.load_application, geo,
        converged=ok, final_load_factor=lam,
        hinge_rotations=dict(rec.peak), yielded=sorted(rec.yielded),
        hinge_detail=rec.detail(), warnings=[warn] if warn else [])
    cache[name] = res
    return res


def run_modal_from_case(eng, num_modes: Optional[int] = None):
    """MODAL eigen solve on the tangent stiffness at the END of the
    nonlinear static case ``model.modal_from_case`` (cached per mode
    count in the engine's modal cache)."""
    from skyframe.engine.opensees_engine import ModalResults
    model = eng.model
    name = model.modal_from_case
    chain = chain_of(model, name)
    geo = _chain_geometric(model, chain)
    case = model.nonlinear_static_cases[name]
    asm = _build_for(eng, case, geo)
    n_massed = asm.free_massed_dofs()
    n = min(num_modes or model.num_modes, n_massed)
    if n <= 0:
        return ModalResults([], [], [], {})
    if n in eng._modal_cache:
        return eng._modal_cache[n]
    _run_prefix(eng, asm, chain)
    eng._seg_span_loads = {}
    eng._seg_fef = {}
    lambdas = eng._solve_eigen(n, n_massed, asm)
    periods = [2.0 * math.pi / math.sqrt(lam) for lam in lambdas]
    freqs = [1.0 / t for t in periods]
    shapes = {mode: {t: list(ops.nodeEigenvector(t, mode))
                     for t in asm.node_coords}
              for mode in range(1, n + 1)}
    part = eng._participation(asm, periods, shapes)
    res = ModalResults(periods, freqs, part, shapes)
    eng._modal_cache[n] = res
    return res
