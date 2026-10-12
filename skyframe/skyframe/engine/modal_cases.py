"""Named modal cases and mass sources — engine side.

CONTRACT "Multiple mass sources, modal cases, Ritz basis".

Every named modal case runs on a SUB-ENGINE: a fresh
:class:`~skyframe.engine.opensees_engine.OpenSeesEngine` on a deep copy
of the source model in which

* the model-level mass fields are replaced by the case's named mass
  source (:func:`skyframe.core.modal_cases.apply_mass_source`),
* ``num_modes`` is the case's mode / vector count and
  ``modal_from_case`` its ``stiffness_case`` (eigen),
* every RS / TH ``modal_case`` / ``initial_case`` reference is cleared
  (the sub-engine runs those cases through the plain legacy code),

and the sub-engine carries the case spec (``_mcx_spec``) so its
``run_modal`` returns Ritz vectors (:mod:`skyframe.engine.ritz_engine`)
for a ``"ritz"`` case.  The mesh — hence every node tag and member uid —
is identical to the parent's (same geometry), so RS / TH results drop
into the parent's result dictionaries unchanged.  Sub-engines are cached
per parent engine (one per modal case).

* RS (``ResponseSpectrumCase.modal_case``): the sub-engine runs the case
  — modal statics with the case's mass map and basis (eigen or Ritz).
* TH direct integration (``TimeHistoryCase.modal_case``): the sub-engine
  runs it — the mass is the case's mass source and the Rayleigh fit uses
  the case's first / third periods (Ritz periods for a Ritz case);
  ``damping_model == "modal"`` keeps its in-domain EIGEN solve.
* FNA uses the case through :mod:`skyframe.engine.fna`.
"""

from __future__ import annotations

import copy
from typing import Callable, Dict, Optional

from skyframe.core import modal_cases as _mcs


def _spec(model, name: str) -> dict:
    mc = getattr(model, "modal_cases", None) or {}
    if name not in mc:
        raise ValueError(f"Unknown modal case {name!r}")
    return _mcs.normalize_modal_case(mc[name])


def prepare_model(model, name: str):
    """A deep copy of ``model`` configured for modal case ``name``."""
    spec = _spec(model, name)
    m = copy.deepcopy(model)
    _mcs.apply_mass_source(m, spec["mass_source"])
    m.num_modes = int(spec["num_modes"])
    m.modal_from_case = spec["stiffness_case"]
    for c in list(m.rs_cases.values()) + list(m.th_cases.values()):
        c.modal_case = ""
    for c in m.th_cases.values():
        c.initial_case = ""
    return m, spec


def attach_spec(eng, spec: dict) -> None:
    """Make ``eng.run_modal`` honour a Ritz spec (eigen: no-op)."""
    if spec.get("type") == "ritz":
        eng._mcx_spec = spec


def sub_engine(eng, name: str):
    """The cached sub-engine of modal case ``name``."""
    from skyframe.engine.opensees_engine import OpenSeesEngine
    subs = eng.__dict__.setdefault("_mcx_subs", {})
    if name not in subs:
        m, spec = prepare_model(eng.source_model, name)
        sub = OpenSeesEngine(m)
        attach_spec(sub, spec)
        subs[name] = sub
    return subs[name]


def run_modal_hook(eng, num_modes: Optional[int]):
    """``run_modal`` hook: Ritz modal results for a sub-engine whose spec
    is a Ritz case, else None (the legacy eigen path runs)."""
    spec = eng.__dict__.get("_mcx_spec")
    if not spec or spec.get("type") != "ritz":
        return None
    from skyframe.engine import ritz_engine as _rtz
    return _rtz.run_modal(eng, num_modes, spec)


def run_modal_case(eng, name: str):
    """ModalResults of a named modal case."""
    sub = sub_engine(eng, name)
    return sub.run_modal()


def run_rs(eng, name: str):
    rs = eng.model.rs_cases[name]
    res = sub_engine(eng, rs.modal_case).run_response_spectrum(name)
    eng._rs_cache[name] = res
    return res


def initial_loads(model, case: str) -> Dict[str, float]:
    """Cumulative pattern factors of the nonlinear static chain ending at
    ``case`` — the gravity stage direct integration applies for an
    ``initial_case`` (load-control chains without hinges only)."""
    from skyframe.core.nonlinear_static import chain_of
    eff: Dict[str, float] = {}
    for n in chain_of(model, case):
        c = model.nonlinear_static_cases[n]
        if c.load_application != "load_control" or c.My \
                or c.default_My is not None or c.hinges == "asce41":
            raise NotImplementedError(
                f"initial_case {case!r}: direct integration can continue "
                "only from load-control nonlinear static cases without "
                "hinges (FNA supports any) — use run_fna")
        for p, f in c.pattern_factors().items():
            eff[p] = eff.get(p, 0.0) + f
    return eff


def run_th(eng, name: str):
    th = eng.model.th_cases[name]
    if th.modal_case:
        sub = sub_engine(eng, th.modal_case)
    else:
        from skyframe.engine.opensees_engine import OpenSeesEngine
        m = copy.deepcopy(eng.source_model)
        m.th_cases[name].initial_case = ""
        sub = OpenSeesEngine(m)
    if th.initial_case:
        # direct integration: the predecessor's loads become the gravity
        # stage (applied statically first and held — the legacy rule)
        sub.model.th_cases[name].gravity = initial_loads(eng.model,
                                                         th.initial_case)
        sub._th_cache.pop(name, None)
    res = sub.run_time_history(name)
    eng._th_cache[name] = res
    return res


def plan(eng, status: Dict[str, str], need) -> None:
    """Set Load Cases to Run: named modal case -> stiffness_case, TH ->
    initial_case (transitive nonlinear static chains re-resolved)."""
    model = eng.model
    added = False
    live = ("finished", "run_as_dependency")
    for n, mc in (getattr(model, "modal_cases", None) or {}).items():
        sc = mc.get("stiffness_case")
        if status.get(n) in live and sc and status.get(sc) == "not_run":
            need(sc, n)
            added = True
    for n, th in model.th_cases.items():
        ic = getattr(th, "initial_case", "")
        if status.get(n) in live and ic and status.get(ic) == "not_run":
            need(ic, n)
            added = True
    if added:
        eng._nls_plan(status, need)


def wants_rs(rs) -> bool:
    return bool(getattr(rs, "modal_case", ""))


def wants_th(th) -> bool:
    return bool(getattr(th, "modal_case", "")
                or getattr(th, "initial_case", ""))


def run_all(eng, runs: Callable[[str], bool], attempt) -> Dict[str, dict]:
    """``run()``: every named modal case that runs -> its result dict
    (``ModalResults.to_dict()`` + ``type`` / ``mass_source`` /
    ``warnings``).  Empty dict for a legacy model."""
    out: Dict[str, dict] = {}
    for name in getattr(eng.model, "modal_cases", None) or {}:
        if not runs(name):
            continue
        res = attempt(name, lambda n=name: run_modal_case(eng, n))
        if res is None:
            continue
        spec = _spec(eng.model, name)
        d = res.to_dict()
        d["type"] = spec["type"]
        d["mass_source"] = spec["mass_source"]
        sub = sub_engine(eng, name)
        d["warnings"] = list(sub.__dict__.get("_ritz_warnings", []))
        out[name] = d
    return out
