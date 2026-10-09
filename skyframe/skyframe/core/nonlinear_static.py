"""Nonlinear Static load cases and case chaining (ETABS Define > Load
Cases > Nonlinear Static parity) — model side.

Data on :class:`skyframe.core.model.BuildingModel`:

* ``nonlinear_static_cases`` — name -> :class:`NonlinearStaticCase`;
* ``modal_from_case`` — ``None`` (default: the elastic MODAL eigen solve)
  or the name of a nonlinear static case whose END STATE supplies the
  stiffness of the MODAL case (ETABS "stiffness at end of nonlinear case").

Both are emitted by ``to_dict`` only when used, so models without the
feature serialise byte-identically.  The solver lives in
:mod:`skyframe.engine.nonlinear_static`; see CONTRACT "Nonlinear static
load cases and case chaining".

This module does NOT import :mod:`skyframe.core.model` at import time
(model.py imports it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

NLS_LOAD_APPLICATIONS = ("load_control", "displacement_control")
NLS_GEOMETRIC = ("none", "p_delta", "large_displacement")
NLS_DOFS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
NLS_HINGE_MODES = ("column_base", "all_ends", "asce41")
NLS_HINGE_PARAM_KEYS = ("expected_factor", "rho", "rho_prime", "fy_bar")
NLS_MAX_STEPS = 10000
#: NonlinearStaticCase.geometric -> PushoverCase/LoadCase.geometric
GEOM_TO_PUSHOVER = {"none": "linear", "p_delta": "pdelta",
                    "large_displacement": "corotational"}


def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


@dataclass
class NonlinearStaticCase:
    """One ETABS-style Nonlinear Static load case.

    ``loads``: ``[{"pattern": name, "scale": factor}, ...]`` — the applied
    load vector (a pattern may appear more than once; the scales add).

    ``load_application``:

    * ``"load_control"`` — the full load is applied in ``steps`` equal
      LoadControl increments (final load factor 1.0);
    * ``"displacement_control"`` — the load vector is scaled by the load
      factor that drives the monitored DOF to ``target_disp`` (the TOTAL
      monitored displacement at the end of the case, i.e. including the
      ``start_from`` state) in ``steps`` equal DisplacementControl
      increments.

    Monitored joint: ``control_point`` ``[x, y, z]`` or ``control_story``
    (its diaphragm master, else its lowest-tag node); neither = the
    pushover roof control node (top-story master, else the topmost node).
    ``control_dof`` in UX..RZ.

    ``geometric``: ``"none"`` | ``"p_delta"`` (PDelta transformation) |
    ``"large_displacement"`` (Corotational).  Every case of a chain must
    use the same value (the transformation is a property of the domain).

    Hinges (``hinges``/``My``/``default_My``/``hardening``/
    ``hinge_params``): EXACTLY the :class:`PushoverCase` fields and rules
    (column_base / all_ends Steel01 springs with My / default_My, or
    ``asce41`` member-assigned ``auto_m3`` / ``fiber_pmm`` hinges).  The
    default (no My) inserts no hinge.  Tension/compression-only members,
    gap/hook/isolator links and compression-only springs are nonlinear
    exactly as in every static analysis.

    ``start_from``: another nonlinear static case whose END STATE is the
    initial condition (recursive chains allowed; cycles rejected).  The
    whole chain is solved in ONE OpenSees domain built with THIS case's
    hinge definition; earlier stages are held with ``loadConst -time 0``.
    """

    name: str
    loads: List[dict] = field(default_factory=list)
    load_application: str = "load_control"
    steps: int = 10
    control_point: Optional[List[float]] = None
    control_story: Optional[str] = None
    control_dof: str = "UX"
    target_disp: Optional[float] = None
    geometric: str = "none"
    start_from: Optional[str] = None
    hinges: str = "column_base"
    My: Dict[str, float] = field(default_factory=dict)
    default_My: Optional[float] = None
    hardening: float = 0.02
    hinge_params: Dict[str, float] = field(default_factory=dict)

    def pattern_factors(self) -> Dict[str, float]:
        """pattern -> summed scale of this case's own loads."""
        out: Dict[str, float] = {}
        for ld in self.loads:
            out[ld["pattern"]] = out.get(ld["pattern"], 0.0) \
                + float(ld["scale"])
        return out

    def to_dict(self) -> dict:
        return {"name": self.name,
                "loads": [{"pattern": str(ld["pattern"]),
                           "scale": float(ld["scale"])} for ld in self.loads],
                "load_application": self.load_application,
                "steps": int(self.steps),
                "control_point": (None if self.control_point is None
                                  else [float(v)
                                        for v in self.control_point]),
                "control_story": self.control_story,
                "control_dof": self.control_dof,
                "target_disp": self.target_disp,
                "geometric": self.geometric,
                "start_from": self.start_from,
                "hinges": self.hinges, "My": dict(self.My),
                "default_My": self.default_My,
                "hardening": self.hardening,
                "hinge_params": dict(self.hinge_params)}


def _coerce_loads(loads) -> List[dict]:
    if isinstance(loads, dict):            # {pattern: scale} shorthand
        loads = [{"pattern": p, "scale": s} for p, s in loads.items()]
    if not isinstance(loads, (list, tuple)):
        raise ValueError("nonlinear static loads must be a list of "
                         "{pattern, scale}")
    out = []
    for ld in loads:
        if not isinstance(ld, dict) or "pattern" not in ld:
            raise ValueError("nonlinear static load entries must be "
                             "{pattern, scale}")
        sc = ld.get("scale", 1.0)
        out.append({"pattern": str(ld["pattern"]),
                    "scale": (float(sc) if _finite(sc) else sc)})
    return out


def _int_steps(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def case_from_dict(name: str, d: dict) -> NonlinearStaticCase:
    if not isinstance(d, dict):
        raise ValueError(f"nonlinear static case {name!r} must be an object")
    cp = d.get("control_point")
    td = d.get("target_disp")
    dmy = d.get("default_My")
    return NonlinearStaticCase(
        name=str(d.get("name", name)),
        loads=_coerce_loads(d.get("loads") or []),
        load_application=str(d.get("load_application", "load_control")),
        steps=_int_steps(d.get("steps", 10)),
        control_point=None if cp is None else list(cp),
        control_story=d.get("control_story"),
        control_dof=str(d.get("control_dof", "UX")),
        target_disp=None if td is None else td,
        geometric=str(d.get("geometric", "none")),
        start_from=d.get("start_from"),
        hinges=str(d.get("hinges", "column_base")),
        My={str(u): v for u, v in (d.get("My") or {}).items()},
        default_My=dmy,
        hardening=d.get("hardening", 0.02),
        hinge_params={str(k): v
                      for k, v in (d.get("hinge_params") or {}).items()})


def add_nonlinear_static_case(model, name: str, loads, **kw
                              ) -> NonlinearStaticCase:
    """Create, validate and store a nonlinear static case."""
    bad = set(kw) - set(NonlinearStaticCase.__dataclass_fields__) - {"name"}
    if bad:
        raise TypeError(f"add_nonlinear_static_case: unknown option(s) "
                        f"{sorted(bad)}")
    d = dict(kw, loads=_coerce_loads(loads))
    case = case_from_dict(name, d)
    case.name = name
    old = model.nonlinear_static_cases.get(name)
    model.nonlinear_static_cases[name] = case
    try:
        validate_nonlinear_static(model)
    except Exception:
        if old is None:
            del model.nonlinear_static_cases[name]
        else:
            model.nonlinear_static_cases[name] = old
        raise
    return case


# --------------------------------------------------------------------------- #
# chains
# --------------------------------------------------------------------------- #
def chain_of(model, name: str) -> List[str]:
    """The chain ``[root, ..., name]`` of nonlinear static cases (each
    starts from the previous one's end state).  Raises ValueError on a
    cycle or an unknown ``start_from``."""
    cases = getattr(model, "nonlinear_static_cases", None) or {}
    chain: List[str] = []
    cur: Optional[str] = name
    while cur is not None:
        if cur not in cases:
            who = chain[-1] if chain else name
            raise ValueError(f"Nonlinear static case {who!r}: "
                             f"start_from references unknown nonlinear "
                             f"static case {cur!r}")
        if cur in chain:
            cyc = list(reversed(chain[chain.index(cur):] + [cur]))
            raise ValueError("Nonlinear static case chain: circular "
                             f"start_from reference {' -> '.join(cyc)}")
        chain.append(cur)
        cur = cases[cur].start_from
    return list(reversed(chain))


def pushover_nls_start(model, po) -> Optional[str]:
    """The nonlinear static case a pushover starts from, or None."""
    sf = getattr(po, "start_from", None)
    if sf and sf in (getattr(model, "nonlinear_static_cases", None) or {}):
        return sf
    return None


def dependencies(model, name: str, kind: str) -> List[str]:
    """Cases a running case of ``kind`` needs (Set Load Cases to Run)."""
    if kind == "nonlinear_static":
        sf = model.nonlinear_static_cases[name].start_from
        return [sf] if sf else []
    if kind == "pushover":
        sf = pushover_nls_start(model, model.pushover_cases[name])
        return [sf] if sf else []
    if kind == "modal":
        mfc = getattr(model, "modal_from_case", None)
        return [mfc] if mfc else []
    return []


# --------------------------------------------------------------------------- #
# validation / (de)serialisation
# --------------------------------------------------------------------------- #
def _validate_case(model, c: NonlinearStaticCase) -> None:
    nm = f"Nonlinear static case {c.name}"
    if not isinstance(c.name, str) or not c.name:
        raise ValueError("Nonlinear static case: name must be a non-empty "
                         "string")
    if not c.loads:
        raise ValueError(f"{nm}: needs at least one load {{pattern, scale}}")
    for ld in c.loads:
        if ld["pattern"] not in model.patterns:
            raise ValueError(f"{nm}: unknown load pattern {ld['pattern']!r}")
        if not _finite(ld["scale"]):
            raise ValueError(f"{nm}: scale of {ld['pattern']!r} must be a "
                             "finite number")
    if c.load_application not in NLS_LOAD_APPLICATIONS:
        raise ValueError(f"{nm}: load_application must be one of "
                         f"{NLS_LOAD_APPLICATIONS}, got "
                         f"{c.load_application!r}")
    if (isinstance(c.steps, bool) or not isinstance(c.steps, int)
            or not 1 <= c.steps <= NLS_MAX_STEPS):
        raise ValueError(f"{nm}: steps must be an integer in "
                         f"[1, {NLS_MAX_STEPS}]")
    if c.geometric not in NLS_GEOMETRIC:
        raise ValueError(f"{nm}: geometric must be one of {NLS_GEOMETRIC}, "
                         f"got {c.geometric!r}")
    if c.control_dof not in NLS_DOFS:
        raise ValueError(f"{nm}: control_dof must be one of {NLS_DOFS}, "
                         f"got {c.control_dof!r}")
    if c.control_point is not None and c.control_story is not None:
        raise ValueError(f"{nm}: give control_point OR control_story, not "
                         "both")
    if c.control_point is not None and not (
            isinstance(c.control_point, (list, tuple))
            and len(c.control_point) == 3
            and all(_finite(v) for v in c.control_point)):
        raise ValueError(f"{nm}: control_point must be [x, y, z]")
    if c.control_story is not None and c.control_story not in {
            s.name for s in model.stories}:
        raise ValueError(f"{nm}: control_story references unknown story "
                         f"{c.control_story!r}")
    if c.load_application == "displacement_control":
        if not (_finite(c.target_disp) and c.target_disp != 0.0):
            raise ValueError(f"{nm}: displacement_control needs a finite "
                             "non-zero target_disp")
    elif c.target_disp is not None and not _finite(c.target_disp):
        raise ValueError(f"{nm}: target_disp must be finite (or None)")
    if c.hinges not in NLS_HINGE_MODES:
        raise ValueError(f"{nm}: hinges must be one of {NLS_HINGE_MODES}, "
                         f"got {c.hinges!r}")
    uids = {m.uid for m in model.members}
    for uid, my in c.My.items():
        if uid not in uids:
            raise ValueError(f"{nm}: My references unknown member {uid!r}")
        if not (_finite(my) and my > 0.0):
            raise ValueError(f"{nm}: My[{uid!r}] must be a finite value > 0")
    if c.default_My is not None and not (_finite(c.default_My)
                                         and c.default_My > 0.0):
        raise ValueError(f"{nm}: default_My must be a finite value > 0 "
                         "(or None)")
    if not (_finite(c.hardening) and 0.0 <= c.hardening < 1.0):
        raise ValueError(f"{nm}: hardening must be in [0, 1)")
    for k, v in c.hinge_params.items():
        if k not in NLS_HINGE_PARAM_KEYS:
            raise ValueError(f"{nm}: unknown hinge_params key {k!r} "
                             f"(allowed: {NLS_HINGE_PARAM_KEYS})")
        if not (_finite(v) and v > 0.0):
            raise ValueError(f"{nm}: hinge_params[{k!r}] must be a finite "
                             "value > 0")
    if c.start_from is not None:
        if c.start_from == c.name:
            raise ValueError(f"{nm}: start_from cannot reference itself "
                             "(cycle)")
        if c.start_from not in model.nonlinear_static_cases:
            raise ValueError(f"{nm}: start_from references unknown "
                             f"nonlinear static case {c.start_from!r}")


def validate_nonlinear_static(model) -> None:
    """Raise ``ValueError`` on malformed nonlinear static cases, chains,
    ``modal_from_case`` or a pushover start_from chain."""
    cases = getattr(model, "nonlinear_static_cases", None)
    if cases is None:
        return
    if not isinstance(cases, dict):
        raise ValueError("nonlinear_static_cases must be an object")
    others = {}
    for kind, src in (("static", model.cases),
                      ("response_spectrum", model.rs_cases),
                      ("time_history", model.th_cases),
                      ("pushover", model.pushover_cases),
                      ("staged", model.staged_cases),
                      ("buckling", model.buckling_cases),
                      ("steady_state",
                       getattr(model, "steady_state_cases", {})),
                      ("psd", getattr(model, "psd_cases", {}))):
        for n in src:
            others.setdefault(n, kind)
    from skyframe.core.model import MODAL_CASE
    for name, c in cases.items():
        if name in others or name == MODAL_CASE:
            raise ValueError(f"Nonlinear static case {name}: the name is "
                             "already used by a "
                             f"{others.get(name, 'modal')} case")
        _validate_case(model, c)
    for name, c in cases.items():
        chain = chain_of(model, name)
        geo = {cases[n].geometric for n in chain}
        if len(geo) > 1:
            raise ValueError(f"Nonlinear static case {name}: every case of "
                             f"the start_from chain {chain} must use the "
                             f"same geometric option (got {sorted(geo)})")
    for po in model.pushover_cases.values():
        sf = pushover_nls_start(model, po)
        if sf is None:
            continue
        g = GEOM_TO_PUSHOVER[cases[sf].geometric]
        if getattr(po, "geometric", "linear") != g:
            raise ValueError(f"Pushover case {po.name}: start_from "
                             f"nonlinear static case {sf!r} uses geometric "
                             f"{cases[sf].geometric!r}; the pushover must "
                             f"use the matching geometric {g!r}")
    mfc = getattr(model, "modal_from_case", None)
    if mfc is not None:
        if not isinstance(mfc, str) or mfc not in cases:
            raise ValueError(f"modal_from_case references unknown nonlinear "
                             f"static case {mfc!r}")


def nls_to_dict(model) -> dict:
    out: dict = {}
    cases = getattr(model, "nonlinear_static_cases", None) or {}
    if cases:
        out["nonlinear_static_cases"] = {k: v.to_dict()
                                         for k, v in cases.items()}
    mfc = getattr(model, "modal_from_case", None)
    if mfc is not None:
        out["modal_from_case"] = mfc
    return out


def nls_from_dict(model, d: dict) -> None:
    raw = d.get("nonlinear_static_cases")
    if raw is not None:
        if not isinstance(raw, dict):
            raise ValueError("nonlinear_static_cases must be an object")
        model.nonlinear_static_cases = {
            str(n): case_from_dict(str(n), cd) for n, cd in raw.items()}
    mfc = d.get("modal_from_case")
    model.modal_from_case = None if mfc is None else str(mfc)
