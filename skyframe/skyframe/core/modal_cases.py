"""Multiple named mass sources and multiple modal cases (ETABS Define >
Mass Source list, Define > Modal Cases list) — model side.

Data on :class:`skyframe.core.model.BuildingModel` (both default ``{}``
and emitted by ``to_dict`` only when non-empty, so legacy models
serialise byte-identically):

* ``mass_sources`` — name -> named mass source::

      {"mass_source": {pattern: factor, ...},   # load patterns x factor
       "mode": "weight" | "element_self_mass",  # self-mass rule
       "options": {self_mass, patterns, include_lateral,
                   include_vertical, lump_at_stories}}

  The model-level legacy fields (``mass_source`` / ``mass_from_patterns``
  / ``mass_source_mode`` / ``mass_options``) remain the DEFAULT mass
  source (name ``""`` everywhere, shown as "MsSrc (default)").  Explicit
  ``story_masses`` overrides, ``nodal_masses`` and diaphragm additional
  masses belong to the structure and apply under every mass source.

* ``modal_cases`` — name -> modal case::

      {"type": "eigen" | "ritz",
       "num_modes": int >= 1,
       "mass_source": "" | <mass_sources name>,
       "stiffness_case": None | <nonlinear static case>,   # eigen only
       "ritz_starts": [{"type": "accel", "dir": "UX"|"UY"|"UZ"|"RX"|"RY"|"RZ"},
                       {"type": "pattern", "name": <pattern>},
                       {"type": "links"}],                 # ritz only
       "max_cycles": int >= 0}                             # ritz, 0 = no cap

  The reserved legacy ``MODAL`` case (``num_modes`` / ``modal_from_case``)
  is unchanged and stays the default for every RS / TH case.

* ``ResponseSpectrumCase.modal_case`` / ``TimeHistoryCase.modal_case``
  (``""`` = the legacy MODAL case; emitted only when set) name the modal
  case whose basis RS, TH (direct integration: mass source, mode count
  and damping fit) and FNA use; ``TimeHistoryCase.initial_case`` (``""``
  = none) names the nonlinear static predecessor whose end state gives
  FNA its gravity initial conditions.

The engine side is :mod:`skyframe.engine.modal_cases` (sub-engines) and
:mod:`skyframe.engine.ritz_engine` (Ritz on the full assembly).  This
module does NOT import :mod:`skyframe.core.model` at import time.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

MODAL_CASE_TYPES = ("eigen", "ritz")
RITZ_START_TYPES = ("accel", "pattern", "links")
RITZ_ACCEL_DIRS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
MASS_SOURCE_MODES = ("weight", "element_self_mass")
MAX_MODAL_MODES = 1000
DEFAULT_RITZ_STARTS = [{"type": "accel", "dir": "UX"},
                       {"type": "accel", "dir": "UY"}]


def _int(v, what: str, lo: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) \
            or not math.isfinite(v) or int(v) != v or int(v) < lo:
        raise ValueError(f"{what} must be an integer >= {lo} (got {v!r})")
    return int(v)


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #
def normalize_mass_source(d) -> dict:
    from skyframe.core.model import MASS_OPTION_DEFAULTS
    if not isinstance(d, dict):
        raise ValueError("a mass source must be an object")
    pats = d.get("mass_source") or {}
    if not isinstance(pats, dict):
        raise ValueError("mass source 'mass_source' must be an object "
                         "{pattern: factor}")
    opts = d.get("options") or {}
    if not isinstance(opts, dict):
        raise ValueError("mass source 'options' must be an object")
    out_opts = {}
    for k, dflt in MASS_OPTION_DEFAULTS.items():
        v = opts.get(k, dflt)
        if not isinstance(v, bool):
            raise ValueError(f"mass source option {k!r} must be true/false")
        out_opts[k] = v
    unknown = set(opts) - set(MASS_OPTION_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown mass source option(s) {sorted(unknown)}")
    out = {"mass_source": {str(p): float(f) for p, f in pats.items()},
           "mode": str(d.get("mode", "weight")),
           "options": out_opts}
    return out


def normalize_modal_case(d) -> dict:
    if not isinstance(d, dict):
        raise ValueError("a modal case must be an object")
    typ = d.get("type", "eigen")
    out = {"type": typ,
           "num_modes": d.get("num_modes", 12),
           "mass_source": str(d.get("mass_source") or ""),
           "stiffness_case": (None if not d.get("stiffness_case")
                              else str(d["stiffness_case"]))}
    if typ == "ritz":
        starts = d.get("ritz_starts")
        out["ritz_starts"] = copy.deepcopy(
            DEFAULT_RITZ_STARTS if starts is None else starts)
        out["max_cycles"] = d.get("max_cycles", 0)
    return out


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate(model) -> None:
    """Validate ``mass_sources`` / ``modal_cases`` and every reference to
    them (called once from ``BuildingModel.validate``)."""
    ms = getattr(model, "mass_sources", None) or {}
    mc = getattr(model, "modal_cases", None) or {}
    if not isinstance(ms, dict) or not isinstance(mc, dict):
        raise ValueError("mass_sources / modal_cases must be objects")
    for name, src in ms.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("mass source names must be non-empty strings")
        n = normalize_mass_source(src)
        if n["mode"] not in MASS_SOURCE_MODES:
            raise ValueError(f"mass source {name!r}: mode must be one of "
                             f"{MASS_SOURCE_MODES} (got {n['mode']!r})")
        for p, f in n["mass_source"].items():
            if p not in model.patterns:
                raise ValueError(f"mass source {name!r}: unknown load "
                                 f"pattern {p!r}")
            if not math.isfinite(f) or f < 0.0:
                raise ValueError(f"mass source {name!r}: factor of {p!r} "
                                 "must be finite and >= 0")
        o = n["options"]
        if not (o["include_lateral"] or o["include_vertical"]):
            raise ValueError(f"mass source {name!r}: include lateral and/or "
                             "vertical mass")
        if not (o["self_mass"] or o["patterns"]):
            raise ValueError(f"mass source {name!r}: choose self mass and/"
                             "or load patterns")
    if not mc:
        _validate_refs(model, mc)
        return
    from skyframe.core.model import MODAL_CASE
    others = {k: v for k, v in model.case_kinds().items()
              if v != "modal" or k == MODAL_CASE}
    nls = getattr(model, "nonlinear_static_cases", None) or {}
    for name, case in mc.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("modal case names must be non-empty strings")
        if name == MODAL_CASE:
            raise ValueError(f"modal case name {MODAL_CASE!r} is reserved "
                             "for the default modal case")
        if name in others:
            raise ValueError(f"modal case {name!r}: the name is already used "
                             f"by a {others[name]} case")
        c = normalize_modal_case(case)
        if c["type"] not in MODAL_CASE_TYPES:
            raise ValueError(f"modal case {name!r}: type must be one of "
                             f"{MODAL_CASE_TYPES} (got {c['type']!r})")
        _int(c["num_modes"], f"modal case {name!r}: num_modes", 1)
        if c["num_modes"] > MAX_MODAL_MODES:
            raise ValueError(f"modal case {name!r}: num_modes must be <= "
                             f"{MAX_MODAL_MODES}")
        if c["mass_source"] and c["mass_source"] not in ms:
            raise ValueError(f"modal case {name!r}: unknown mass source "
                             f"{c['mass_source']!r}")
        if c["stiffness_case"] is not None:
            if c["type"] != "eigen":
                raise ValueError(f"modal case {name!r}: stiffness_case is "
                                 "available for eigen cases only")
            if c["stiffness_case"] not in nls:
                raise ValueError(f"modal case {name!r}: stiffness_case "
                                 f"{c['stiffness_case']!r} is not a "
                                 "nonlinear static case")
        if c["type"] == "ritz":
            _int(c["max_cycles"], f"modal case {name!r}: max_cycles", 0)
            starts = c["ritz_starts"]
            if not isinstance(starts, list) or not starts:
                raise ValueError(f"modal case {name!r}: a Ritz case needs at "
                                 "least one starting load vector")
            for s in starts:
                _validate_start(model, name, s)
    _validate_refs(model, mc)


def _validate_start(model, name: str, s) -> None:
    if not isinstance(s, dict) or s.get("type") not in RITZ_START_TYPES:
        raise ValueError(f"modal case {name!r}: each Ritz start must be "
                         f"{{type: one of {RITZ_START_TYPES}, ...}} "
                         f"(got {s!r})")
    if s["type"] == "accel":
        if s.get("dir") not in RITZ_ACCEL_DIRS:
            raise ValueError(f"modal case {name!r}: acceleration start dir "
                             f"must be one of {RITZ_ACCEL_DIRS}")
    elif s["type"] == "pattern":
        if s.get("name") not in model.patterns:
            raise ValueError(f"modal case {name!r}: unknown load pattern "
                             f"{s.get('name')!r} in the Ritz starts")
    extra = set(s) - {"type", "dir", "name"}
    if extra:
        raise ValueError(f"modal case {name!r}: unknown Ritz start key(s) "
                         f"{sorted(extra)}")


def _validate_refs(model, mc: dict) -> None:
    from skyframe.core.model import MODAL_CASE
    nls = getattr(model, "nonlinear_static_cases", None) or {}
    for kind, cases in (("RS", model.rs_cases), ("TH", model.th_cases)):
        for cname, c in cases.items():
            ref = getattr(c, "modal_case", "") or ""
            if ref and ref != MODAL_CASE and ref not in mc:
                raise ValueError(f"{kind} case {cname!r}: unknown modal case "
                                 f"{ref!r}")
    for cname, c in model.th_cases.items():
        ic = getattr(c, "initial_case", "") or ""
        if ic and ic not in nls:
            raise ValueError(f"TH case {cname!r}: initial_case {ic!r} is not "
                             "a nonlinear static case")
    from skyframe.core import thfunc as _thf
    for fname, fn in model.th_functions.items():
        _thf.validate_source(fname, getattr(fn, "source", None))


# --------------------------------------------------------------------------- #
# (de)serialisation hooks
# --------------------------------------------------------------------------- #
def model_to_dict(model) -> dict:
    out = {}
    ms = getattr(model, "mass_sources", None) or {}
    mc = getattr(model, "modal_cases", None) or {}
    if ms:
        out["mass_sources"] = {k: normalize_mass_source(v)
                               for k, v in ms.items()}
    if mc:
        out["modal_cases"] = {k: normalize_modal_case(v)
                              for k, v in mc.items()}
    return out


def model_from_dict(model, d: dict) -> None:
    ms = d.get("mass_sources")
    if ms is not None:
        if not isinstance(ms, dict):
            raise ValueError("mass_sources must be an object")
        model.mass_sources = {str(k): normalize_mass_source(v)
                              for k, v in ms.items()}
    mc = d.get("modal_cases")
    if mc is not None:
        if not isinstance(mc, dict):
            raise ValueError("modal_cases must be an object")
        model.modal_cases = {str(k): normalize_modal_case(v)
                             for k, v in mc.items()}
    for key, cases in (("rs_cases", model.rs_cases),
                       ("th_cases", model.th_cases)):
        for cname, cd in (d.get(key) or {}).items():
            if cname in cases and isinstance(cd, dict):
                cases[cname].modal_case = str(cd.get("modal_case") or "")
                if key == "th_cases":
                    cases[cname].initial_case = str(
                        cd.get("initial_case") or "")
    for fname, fd in (d.get("th_functions") or {}).items():
        if fname in model.th_functions and isinstance(fd, dict) \
                and fd.get("source") is not None:
            model.th_functions[fname].source = copy.deepcopy(fd["source"])


# --------------------------------------------------------------------------- #
# helpers shared with the engine / UI
# --------------------------------------------------------------------------- #
def default_mass_source(model) -> dict:
    """The legacy model-level mass source in the named-source shape."""
    return normalize_mass_source({
        "mass_source": dict(model.effective_mass_source()),
        "mode": getattr(model, "mass_source_mode", "weight"),
        "options": model.effective_mass_options()})


def apply_mass_source(model, name: str) -> None:
    """Replace the model-level mass fields of ``model`` (a COPY) by the
    named mass source ``name`` (``""`` = keep the default)."""
    if not name:
        return
    src = normalize_mass_source(model.mass_sources[name])
    model.mass_source = dict(src["mass_source"])
    model.mass_from_patterns = {}
    model.mass_source_mode = src["mode"]
    model.mass_options = dict(src["options"])


def modal_case_dependencies(model, name: str, kind: str) -> List[str]:
    """Cases a running case needs (Set Load Cases to Run)."""
    from skyframe.core.model import MODAL_CASE
    mc = getattr(model, "modal_cases", None) or {}
    if kind == "modal" and name in mc:
        sc = mc[name].get("stiffness_case")
        return [sc] if sc else []
    if kind == "response_spectrum":
        ref = getattr(model.rs_cases[name], "modal_case", "") or ""
        return [ref or MODAL_CASE]
    if kind == "time_history":
        th = model.th_cases[name]
        ref = getattr(th, "modal_case", "") or ""
        out = [ref or MODAL_CASE]
        if getattr(th, "initial_case", ""):
            out.append(th.initial_case)
        return out
    return []


def modal_dep(model, case_name: str) -> str:
    """The modal case an RS / TH / frequency case depends on."""
    from skyframe.core.model import MODAL_CASE
    for cases in (model.rs_cases, model.th_cases):
        if case_name in cases:
            return getattr(cases[case_name], "modal_case", "") or MODAL_CASE
    return MODAL_CASE


def rename_pattern(model, old: str, new: str) -> None:
    """Follow a load-pattern rename into the named mass sources and the
    Ritz pattern starts."""
    for src in (getattr(model, "mass_sources", None) or {}).values():
        pats = src.get("mass_source") or {}
        if old in pats:
            src["mass_source"] = {(new if k == old else k): v
                                  for k, v in pats.items()}
    for mc in (getattr(model, "modal_cases", None) or {}).values():
        for s in mc.get("ritz_starts") or []:
            if s.get("type") == "pattern" and s.get("name") == old:
                s["name"] = new
