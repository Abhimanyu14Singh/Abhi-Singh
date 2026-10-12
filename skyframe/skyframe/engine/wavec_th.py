"""Wave C time-history routing: Linear Modal History / FNA case types and
direct-integration initial conditions from a staged case or a hinged /
displacement-controlled nonlinear static chain.

CONTRACT "Eigen shift / cutoff / tolerance, modal history and FNA case
types, TH initial conditions, RS / steady-state modal damping".

Case types (``TimeHistoryCase.th_type``, :mod:`skyframe.core.wavec_dyn`):

* ``"fna"`` — :meth:`OpenSeesEngine.run_time_history` (hence ``run()``,
  Run All, Set Load Cases to Run and combinations) returns the case's
  :meth:`run_fna` result;
* ``"modal"`` — linear modal superposition: every device link is replaced
  by its linear part (the FNA linearisation of :func:`skyframe.engine.fna.
  _devices`: isolator ``k1``, friction pendulum stick + ``N/R``, multilinear
  initial slope, dampers / gaps / hooks removed) and the device-free FNA
  integration runs on that model — exact modal superposition (Newmark
  average acceleration per mode, the case's damping model).
* ``"direct"`` — unchanged.

Direct-integration initial conditions (``initial_case``):

* a nonlinear static chain that the legacy rule accepts (load control, no
  hinges) keeps the legacy path: its cumulative loads become the gravity
  stage (bit-identical);
* any other nonlinear static chain (hinged and/or displacement-controlled,
  geometric ``"none"``) is REPLAYED inside the TH domain
  (:func:`skyframe.engine.nonlinear_static.run_start_state`) — the hinges
  of the TH domain are the TH case's own when it defines any (``My`` /
  ``default_My`` / ``asce41``), else the predecessor's — and held
  (``loadConst``); Newton is used;
* a STAGED case: the staged END displacements ``u_s`` (by coordinates;
  diaphragm masters by story) are imposed with ``setNodeDisp``, one zero
  Linear step updates every element to ``u_s``, and the internal forces
  ``F(u_s)`` are applied as constant nodal loads, so the domain starts AT
  ``u_s`` in equilibrium (elements carry the full-structure forces of
  ``u_s``; staged locked-in self-equilibrated forces are not represented).

``THResults.extra["initial_state"] = {"case", "kind": "nonlinear_static" |
"staged", "hinges_from"?, "max_disp_mismatch"?}``; the reported series are
increments past the initial state (the gravity-stage convention).
"""

from __future__ import annotations

import copy
from typing import Dict, Optional

import numpy as np
import openseespy.opensees as ops

from skyframe.core import wavec_dyn as _wcd

_PAT_A = 9101
_PAT_B = 9102


# --------------------------------------------------------------------------- #
# case types
# --------------------------------------------------------------------------- #
def typed(th) -> bool:
    return th is not None and _wcd.th_type(th) != "direct"


def run_typed(eng, name: str):
    """Result of a modal / FNA typed TH case (cached in ``_th_cache``)."""
    from skyframe.engine.opensees_engine import OpenSeesEngine
    th = eng.model.th_cases[name]
    t = _wcd.th_type(th)
    if t == "fna":
        res = eng.run_fna(name)
    else:
        from skyframe.engine import fna as _fnx
        lin, _recs = _fnx._devices(eng, th, eng.model, None)
        res = OpenSeesEngine(lin).run_fna(name)
    res.extra = dict(res.extra, th_type=t)
    eng._th_cache[name] = res
    return res


# --------------------------------------------------------------------------- #
# initial conditions (direct integration)
# --------------------------------------------------------------------------- #
def _has_hinges(case) -> bool:
    return bool(getattr(case, "My", None)) or (
        getattr(case, "default_My", None) is not None) or (
        getattr(case, "hinges", "") == "asce41")


def staged_state(eng, ic: str) -> dict:
    """``{"disp": {pkey: 6}, "master": {story: 6}}`` of a staged case's
    end state (run on ``eng``; tags mapped through its standard build)."""
    from skyframe.engine.opensees_engine import _pkey
    res = eng.run_staged(ic)
    asm = eng._build()
    nd = res.case.node_disp
    disp = {}
    for t, c in asm.node_coords.items():
        if t in nd:
            disp.setdefault(_pkey(c), [float(v) for v in nd[t]])
    master = {s: [float(v) for v in nd[t]] for s, t in asm.masters.items()
              if t in nd}
    return {"disp": disp, "master": master}


def prepare_initial(eng, sub, name: str, ic: str) -> None:
    """``engine.modal_cases.run_th`` hook: set up the initial state of TH
    ``name`` on ``sub`` (legacy gravity rule when it applies)."""
    from skyframe.engine import modal_cases as _mcx
    model = eng.model
    if ic in model.staged_cases:
        st = staged_state(eng, ic)
        sub.__dict__.setdefault("_wth_init", {})[name] = {
            "case": ic, "kind": "staged", **st}
        return
    try:
        sub.model.th_cases[name].gravity = _mcx.initial_loads(model, ic)
    except NotImplementedError:
        from skyframe.core.nonlinear_static import chain_of
        chain = chain_of(model, ic)
        geo = {model.nonlinear_static_cases[n].geometric for n in chain}
        if geo != {"none"}:
            raise ValueError(f"TH case {name!r}: initial_case {ic!r} uses "
                             f"geometric {sorted(geo)}; direct integration "
                             "continues from geometric 'none' chains only")
        sub.model.th_cases[name].gravity = {}
        sub.__dict__.setdefault("_wth_init", {})[name] = {
            "case": ic, "kind": "nonlinear_static"}


def _init(eng, name: str) -> Optional[dict]:
    return (eng.__dict__.get("_wth_init") or {}).get(name)


def hinge_case(eng, th, nonlinear: bool):
    """The hinge case of the TH build (legacy: ``th`` when nonlinear)."""
    ini = _init(eng, th.name)
    if ini is None or ini["kind"] != "nonlinear_static":
        return th if nonlinear else None
    if nonlinear and _has_hinges(th):
        return th
    pre = eng.model.nonlinear_static_cases[ini["case"]]
    if _has_hinges(pre):
        return pre
    return th if nonlinear else None


def forces_newton(eng, name: str) -> bool:
    return _init(eng, name) is not None


def start(eng, asm, th, name: str) -> Optional[dict]:
    """Run the initial state inside the live TH domain; returns the
    ``extra["initial_state"]`` dict, or None (legacy)."""
    ini = _init(eng, name)
    if ini is None:
        return None
    if ini["kind"] == "nonlinear_static":
        from skyframe.engine import nonlinear_static as _nls
        _nls.run_start_state(eng, asm, ini["case"])
        hc = hinge_case(eng, th, bool(getattr(th, "nonlinear", False)))
        return {"case": ini["case"], "kind": "nonlinear_static",
                "hinges_from": (None if hc is None else
                                ("time_history" if hc is th
                                 else "initial_case"))}
    return _impose_staged(eng, asm, ini)


def _impose_staged(eng, asm, ini: dict) -> dict:
    from skyframe.engine import solver_policy as _slv
    from skyframe.engine.opensees_engine import _REUSE, _pkey
    disp, master = ini["disp"], ini["master"]
    # every domain node by coordinates (hinge / panel-zone / release
    # duplicates share their joint's coordinates -> zero spring
    # deformation); grounded spring / anchor nodes stay put
    ground = set(getattr(asm, "anchor_tags", ()) or ())
    for lst in (getattr(asm, "spring_ground", None) or {}).values():
        ground.update(g for g, _d in lst)
    masters = set(asm.masters.values())
    target: Dict[int, list] = {}
    for t in ops.getNodeTags():
        if t in ground or t in masters:
            continue
        u = disp.get(_pkey(tuple(ops.nodeCoord(t))))
        if u is not None:
            target[t] = u
    for s, t in asm.masters.items():
        if s in master:
            target[t] = master[s]
    for t, u in target.items():
        for d in range(6):
            if u[d] != 0.0:
                ops.setNodeDisp(t, d + 1, float(u[d]), "-commit")
    tags = sorted(ops.getNodeTags())

    def setup(alg: str) -> None:
        _REUSE["factored"] = None
        ops.wipeAnalysis()
        ops.constraints("Transformation" if asm.use_transformation
                        else "Plain")
        _slv.apply_system(asm)
        ops.test("NormDispIncr", 1.0e-8, 25)
        ops.algorithm(alg)
        ops.integrator("LoadControl", 0.0)
        ops.analysis("Static")

    # 1) stale resisting forces as loads -> one zero Linear step updates
    #    every element (material elements included) to u_s
    ops.reactions()
    ops.timeSeries("Constant", _PAT_A)
    ops.pattern("Plain", _PAT_A, _PAT_A)
    for t in tags:
        r = ops.nodeReaction(t)
        if any(r):
            ops.load(t, *[float(v) for v in r])
    setup("Linear")
    ops.analyze(1)
    ops.remove("loadPattern", _PAT_A)
    # 2) the true internal forces F(u_s) as constant loads -> equilibrium
    ops.reactions()
    ops.timeSeries("Constant", _PAT_B)
    ops.pattern("Plain", _PAT_B, _PAT_B)
    for t in tags:
        r = ops.nodeReaction(t)
        if any(r):
            ops.load(t, *[float(v) for v in r])
    setup("Newton")
    if ops.analyze(1) != 0:
        raise RuntimeError(f"initial state from staged case "
                           f"{ini['case']!r} did not converge")
    ops.loadConst("-time", 0.0)
    mis = 0.0
    for t, u in target.items():
        cur = ops.nodeDisp(t)
        mis = max(mis, max(abs(float(cur[d]) - u[d]) for d in range(6)))
    return {"case": ini["case"], "kind": "staged",
            "max_disp_mismatch": float(mis)}


def finish(result, info: Optional[dict], asm) -> None:
    """Attach ``extra["initial_state"]``; a domain with hinges from the
    predecessor reports its hinge rotations like a nonlinear case."""
    if info is None:
        return
    result.extra = dict(result.extra, initial_state=dict(info))
    if asm.hinge_ele and not result.nonlinear:
        result.nonlinear = True


# --------------------------------------------------------------------------- #
# FNA: staged initial state
# --------------------------------------------------------------------------- #
def fna_staged_disp_at(eng, th):
    """``engine.fna._initial_state`` hook: ``disp_at(point)`` of a staged
    ``initial_case`` (None when the case is not staged)."""
    ic = getattr(th, "initial_case", "") or ""
    if not ic or ic not in eng.model.staged_cases:
        return None
    from skyframe.engine.opensees_engine import _pkey
    st = staged_state(eng, ic)
    disp = st["disp"]

    def disp_at(point):
        return disp.get(_pkey(tuple(point)))
    return disp_at


__all__ = ["typed", "run_typed", "prepare_initial", "hinge_case",
           "forces_newton", "start", "finish", "fna_staged_disp_at"]
