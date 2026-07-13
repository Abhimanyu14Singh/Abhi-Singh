"""SkyFrame public Python API — script SkyFrame without the web UI.

This module is the stable, documented facade over the SkyFrame machinery
(the ETABS-OAPI counterpart): open/save models, run analyses, run the
design checkers, and pull flat result tables — all as THIN wrappers over
the same functions the HTTP endpoints call, with no logic of their own.

Quick start::

    import skyframe.client as sky

    model = sky.quick_building(stories=4)
    results = sky.run(model)
    for row in sky.to_dataframe(results, "drifts"):
        print(row["case"], row["story"], row["drift_x"])

See ``docs/PUBLIC_API.md`` for the full tour with runnable examples.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from .core.builder import quick_building            # noqa: F401  (re-export)
from .core.model import BuildingModel
from .engine.opensees_engine import (AnalysisResults, ModalResults,
                                     OpenSeesEngine, PushoverResults,
                                     THResults)

__all__ = [
    "quick_building", "open_model", "save_model",
    "run", "run_modal", "run_ritz", "run_fna", "run_pushover",
    "run_cracked",
    "design_steel", "design_concrete", "design_wall", "design_punching",
    "to_dataframe",
]


# --------------------------------------------------------------------- files
def open_model(path: str) -> BuildingModel:
    """Load a saved SkyFrame model (the ``.skyframe.json`` format).

    The same reader as the web UI's model gallery
    (``BuildingModel.from_dict`` on the JSON payload).  Raises
    ``ValueError``/``KeyError``/``TypeError`` on an invalid file and
    ``OSError`` when the path cannot be read.
    """
    with open(path, "r", encoding="utf-8") as fh:
        return BuildingModel.from_dict(json.load(fh))


def save_model(model: BuildingModel, path: str) -> str:
    """Write ``model`` to ``path`` as JSON (round-trips via ``open_model``).

    Returns the path written.
    """
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(model.to_dict(), fh)
    return path


# ------------------------------------------------------------------ analysis
def run(model: BuildingModel) -> AnalysisResults:
    """Run EVERY analysis the model defines and return the full bundle.

    Static cases, combos, modal, response-spectrum, time-history (under
    the step cap), pushover (under the step cap), staged, buckling, plus
    all the derived reports (story props, takedown, section cuts, piers,
    deflection checks, seismic diagnostics).  Equivalent to the web UI's
    Analyze button; ``results.to_dict()`` is exactly the ``POST
    /api/analyze`` payload.
    """
    return OpenSeesEngine(model).run()


def run_modal(model: BuildingModel,
              num_modes: Optional[int] = None) -> ModalResults:
    """Eigenvalue analysis only: periods, frequencies, shapes,
    participation.  ``num_modes`` defaults to ``model.num_modes``."""
    return OpenSeesEngine(model).run_modal(num_modes)


def run_ritz(model: BuildingModel, n: Optional[int] = None,
             direction: str = "X"):
    """Load-dependent Ritz vectors (``skyframe.core.ritz.RitzResults``).

    ``n`` vectors (default ``model.num_modes``) seeded by the
    mass-proportional lateral load in ``direction`` ("X" | "Y" | "XY").
    """
    return OpenSeesEngine(model).run_ritz(n, direction)


def run_fna(model: BuildingModel, case: str) -> THResults:
    """Fast Nonlinear Analysis of one time-history case (``model.
    th_cases[case]``); nonlinear links ride modal superposition."""
    return OpenSeesEngine(model).run_fna(case)


def run_pushover(model: BuildingModel, case: str) -> PushoverResults:
    """Nonlinear static pushover of ``model.pushover_cases[case]``."""
    return OpenSeesEngine(model).run_pushover(case)


def run_cracked(model: BuildingModel, case: str,
                cracked_ratio: float = 0.35, fr_factor: float = 0.62,
                max_iter: int = 10, tol: float = 0.02):
    """Iterative cracked-slab stiffness solve of one static case
    (``skyframe.core.cracked.CrackedResults``); see CONTRACT v0.25."""
    return OpenSeesEngine(model).run_cracked(
        case, cracked_ratio=cracked_ratio, fr_factor=fr_factor,
        max_iter=max_iter, tol=tol)


# -------------------------------------------------------------------- design
def _results_or_run(model: BuildingModel,
                    results: Optional[AnalysisResults]) -> AnalysisResults:
    return results if results is not None else OpenSeesEngine(model).run()


def design_steel(model: BuildingModel, case: Optional[str] = None,
                 combos: Union[None, bool, List[str]] = None,
                 code: Optional[str] = None,
                 results: Optional[AnalysisResults] = None,
                 **kw: float) -> Dict[str, Any]:
    """Steel member checks — AISC 360 (default) or EC3 (``code="EC3"``).

    Mirrors ``POST /api/design/steel``: pass ``case`` (one case/combo
    name) or ``combos`` (``True`` for every additive combo, or a list of
    names); optional overrides ``Fy`` (kPa; read as fy for EC3), ``kx``,
    ``ky``, ``Lb`` (m; AISC only).  ``results`` reuses an existing
    :func:`run` bundle instead of re-running the analysis.  Returns
    ``{"preliminary", "case", "combos", "checks": [...], "summary"}`` —
    the endpoint payload shape.
    """
    from .design.steel import (check_members, check_members_envelope,
                               summarize)
    if code is not None and code not in ("AISC360", "EC3"):
        raise ValueError("code must be 'AISC360' or 'EC3'")
    if not combos and not case:
        raise ValueError("'case' (a case/combo name) or 'combos' is "
                         "required")
    src = _results_or_run(model, results)
    if code == "EC3":
        from .design.steel_ec3 import (check_members_ec3,
                                       check_members_ec3_envelope,
                                       summarize_ec3)
        kw3: Dict[str, float] = {}
        if "Fy" in kw:
            kw3["fy"] = kw["Fy"]
        for k in ("kx", "ky"):
            if k in kw:
                kw3[k] = kw[k]
        if combos:
            names = combos if isinstance(combos, list) else None
            checks = check_members_ec3_envelope(model, src, combos=names,
                                                **kw3)
        else:
            checks = check_members_ec3(model, src, case, **kw3)
        summ = summarize_ec3(checks)
    elif combos:
        names = combos if isinstance(combos, list) else None
        checks = check_members_envelope(model, src, combos=names, **kw)
        summ = summarize(checks)
    else:
        checks = check_members(model, src, case, **kw)
        summ = summarize(checks)
    payload: Dict[str, Any] = {
        "preliminary": True, "case": case, "combos": combos,
        "checks": [c.to_dict() for c in checks], "summary": summ}
    if code is not None:
        payload["code"] = code
    return payload


def design_concrete(model: BuildingModel, rebar: Dict[str, dict],
                    case: Optional[str] = None,
                    combos: Union[None, bool, List[str]] = None,
                    fc: Optional[float] = None,
                    code: Optional[str] = None,
                    results: Optional[AnalysisResults] = None
                    ) -> Dict[str, Any]:
    """Concrete member checks — ACI 318 (default) or EC2 (``code="EC2"``).

    Mirrors ``POST /api/design/concrete``: ``rebar`` maps member uid ->
    ``RebarLayout`` fields (e.g. ``{"As_top": 8e-4, "As_bot": 8e-4}``);
    ``fc`` (kPa) overrides the E-derived concrete strength (read as fck
    for EC2).  ``case``/``combos``/``results`` as in
    :func:`design_steel`.  Returns the endpoint payload shape.
    """
    from .design.concrete import (RebarLayout, check_concrete_members,
                                  check_concrete_members_envelope)
    from .design.concrete import summarize as summ_c
    if code is not None and code not in ("ACI318", "EC2"):
        raise ValueError("code must be 'ACI318' or 'EC2'")
    if not combos and not case:
        raise ValueError("'case' or 'combos' is required")
    layouts = {uid: (fields if isinstance(fields, RebarLayout)
                     else RebarLayout(**fields))
               for uid, fields in rebar.items()}
    src = _results_or_run(model, results)
    names = combos if isinstance(combos, list) else None
    if code == "EC2":
        from .design.concrete_ec2 import (check_concrete_members_ec2,
                                          check_concrete_members_ec2_envelope,
                                          summarize_ec2)
        kwe = {"fck": fc} if fc is not None else {}
        if combos:
            checks = check_concrete_members_ec2_envelope(
                model, src, layouts, combos=names, **kwe)
        else:
            checks = check_concrete_members_ec2(model, src, case, layouts,
                                                **kwe)
        summ = summarize_ec2(checks)
    else:
        kwa = {"fc": fc} if fc is not None else {}
        if combos:
            checks = check_concrete_members_envelope(
                model, src, layouts, combos=names, **kwa)
        else:
            checks = check_concrete_members(model, src, case, layouts,
                                            **kwa)
        summ = summ_c(checks)
    payload: Dict[str, Any] = {
        "preliminary": True, "case": case, "combos": combos,
        "checks": [c.to_dict() for c in checks], "summary": summ}
    if code is not None:
        payload["code"] = code
    return payload


def design_wall(model: BuildingModel,
                combos: Optional[List[str]] = None,
                results: Optional[AnalysisResults] = None,
                **kw: float) -> Dict[str, Any]:
    """ACI 318 shear-wall pier checks (mirrors ``POST /api/design/wall``).

    ``combos`` defaults to every additive combo; optional overrides
    ``rho_v``, ``rho_h``, ``fy``, ``fc_prime``.  The model needs
    pier-labeled walls (``ShellRegion.pier``).  Returns
    ``{"preliminary", "combos", "piers": [...], "summary"}``.
    """
    from .design.wall import check_wall_piers, summarize_walls
    src = _results_or_run(model, results)
    checks = check_wall_piers(model, src, combos=combos, **kw)
    return {"preliminary": True, "combos": combos,
            "piers": [c.to_dict() for c in checks],
            "summary": summarize_walls(checks)}


def design_punching(model: BuildingModel, case: Optional[str] = None,
                    fc_prime: Optional[float] = None,
                    cover: Optional[float] = None,
                    results: Optional[AnalysisResults] = None
                    ) -> Dict[str, Any]:
    """ACI two-way (punching) shear checks at slab columns (mirrors
    ``POST /api/design/punching``).

    ``case`` defaults to the first DEAD-classified case; ``fc_prime``
    (kPa) and ``cover`` (m) override the defaults.  Models without
    meshed shell slabs return an empty ``columns`` list.  Returns
    ``{"preliminary", "case", "columns": [...]}``.
    """
    from .design.punching import check_punching
    if not any(r.kind == "slab" and r.behavior == "shell"
               for r in model.shells):
        if case is not None and case not in model.cases \
                and case not in model.combos:
            raise KeyError(f"case/combo {case!r} not found")
        return {"preliminary": True, "case": case, "columns": []}
    src = _results_or_run(model, results)
    kw = {}
    if fc_prime is not None:
        kw["fc_prime"] = fc_prime
    if cover is not None:
        kw["cover"] = cover
    checks = check_punching(model, src, case, **kw)
    return {"preliminary": True,
            "case": checks[0].case if checks else case,
            "columns": [c.to_dict() for c in checks]}


# -------------------------------------------------------------- flat tables
_END_KEYS = ("N", "V2", "V3", "T", "M2", "M3")


def to_dataframe(results: Union[AnalysisResults, Dict[str, Any]],
                 table: str) -> List[Dict[str, Any]]:
    """Flatten results into a list of row dicts (pandas-free "dataframe").

    Feed the return value straight to ``pandas.DataFrame(rows)`` if you
    do use pandas.  Tables over an :class:`AnalysisResults` from
    :func:`run` (cases AND additive/envelope combos):

    * ``"drifts"`` — one row per case per story:
      ``{case, story, ux, uy, drift_x, drift_y, shear_x, shear_y}``
      (m, ratios, kN; stories bottom -> top).
    * ``"reactions"`` — one row per case per support node:
      ``{case, node, x, y, z, FX, FY, FZ, MX, MY, MZ}`` (kN, kN*m).
    * ``"member_forces"`` — one row per case per member END:
      ``{case, member, story, end ("i"|"j"), N, V2, V3, T, M2, M3}``
      (local axes, kN / kN*m; N +compression at end i).

    ``"design"`` flattens the payload of any ``design_*`` helper (pass
    that dict as ``results``): one row per check entry, whichever of
    ``checks`` / ``piers`` / ``columns`` the payload carries.
    """
    if table == "design":
        if not isinstance(results, dict):
            raise TypeError("table 'design' expects the dict returned by "
                            "a design_* helper")
        for key in ("checks", "piers", "columns"):
            if key in results:
                return [dict(row) for row in results[key]]
        raise ValueError("design payload has no checks/piers/columns")

    if not isinstance(results, AnalysisResults):
        raise TypeError(f"table {table!r} expects an AnalysisResults "
                        "(from skyframe.client.run)")
    sources = list(results.cases.items()) + list(results.combos.items())

    if table == "drifts":
        rows = []
        for cname, cr in sources:
            for story in results.story_order:
                st = cr.story.get(story)
                if st is None:
                    continue
                rows.append({"case": cname, "story": story, **st})
        return rows

    if table == "reactions":
        rows = []
        for cname, cr in sources:
            for tag in sorted(cr.reactions):
                r = cr.reactions[tag]
                x, y, z = results.nodes.get(tag, (None, None, None))
                rows.append({"case": cname, "node": tag,
                             "x": x, "y": y, "z": z,
                             "FX": r[0], "FY": r[1], "FZ": r[2],
                             "MX": r[3], "MY": r[4], "MZ": r[5]})
        return rows

    if table == "member_forces":
        story_of = {m["uid"]: m.get("story", "") for m in results.members}
        rows = []
        for cname, cr in sources:
            for uid, f in cr.member_forces.items():
                for end, off in (("i", 0), ("j", 6)):
                    rows.append({"case": cname, "member": uid,
                                 "story": story_of.get(uid, ""), "end": end,
                                 **{k: f[off + i]
                                    for i, k in enumerate(_END_KEYS)}})
        return rows

    raise ValueError(f"unknown table {table!r}; expected 'drifts', "
                     "'reactions', 'member_forces' or 'design'")
