"""Model summary / audit trail (ETABS File > Model Info, Analyze > Analysis
Log run times) and the report-data aggregate — CONTRACT "Model info, run
log and report data".

* :func:`model_info` — pure counts / units / extents of a model.
* :func:`build_run_log` — the audit record of one ``/api/analyze`` run:
  wall time, per-case run times (``OpenSeesEngine.case_times``), case /
  combo status and every warning (the results ``warning`` string split
  into items, per-case ``warnings`` lists and Python warnings raised
  during the run), de-duplicated in first-seen order.
* :func:`report_data` — model info + run log + selected analysis tables
  (``skyframe.core.tables``) for a report, from stored results only.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, Iterable, List, Optional

RUN_LOG_VERSION = 1


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _n(d) -> int:
    return len(d or {})


def model_info(model) -> Dict[str, Any]:
    """{name, units, counts{...}, members_by_kind, height, plan, ...}."""
    kinds: Dict[str, int] = {}
    for m in model.members:
        kinds[m.kind] = kinds.get(m.kind, 0) + 1
    shells_by_kind: Dict[str, int] = {}
    for r in model.shells:
        shells_by_kind[r.kind] = shells_by_kind.get(r.kind, 0) + 1
    joints = set()
    for m in model.members:
        joints.add(tuple(round(v, 6) for v in m.pi))
        joints.add(tuple(round(v, 6) for v in m.pj))
    for r in model.shells:
        for c in r.corners:
            joints.add(tuple(round(v, 6) for v in c))
    try:
        lx, ly = model.plan_extents()
    except Exception:                                  # pragma: no cover
        lx = ly = 0.0
    n_loads = 0
    for p in model.patterns.values():
        n_loads += (len(p.member_udls) + len(p.member_loads)
                    + len(p.nodal_loads) + len(p.story_forces)
                    + len(p.area_loads) + len(p.thermal_loads))
    counts = {
        "stories": len(model.stories),
        "joints": len(joints),
        "frames": len(model.members),
        "shells": len(model.shells),
        "links": len(model.links),
        "materials": _n(model.materials),
        "frame_sections": _n(model.sections),
        "shell_sections": _n(model.shell_sections),
        "load_patterns": _n(model.patterns),
        "load_cases": _n(model.cases),
        "load_combos": _n(model.combos),
        "rs_cases": _n(model.rs_cases),
        "th_cases": _n(model.th_cases),
        "pushover_cases": _n(model.pushover_cases),
        "staged_cases": _n(model.staged_cases),
        "buckling_cases": _n(model.buckling_cases),
        "nonlinear_static_cases": _n(getattr(
            model, "nonlinear_static_cases", None)),
        "supports": len(model.supports),
        "spring_supports": len(model.spring_supports),
        "groups": _n(getattr(model, "groups", None)),
        "section_cuts": len(model.section_cuts),
        "assigned_loads": n_loads,
        "open_wind_members": sum(1 for m in model.members
                                 if getattr(m, "open_wind", None)),
    }
    return {"name": model.name, "units": "kN, m, C (SI store)",
            "display_units": model.display_units,
            "counts": counts, "members_by_kind": kinds,
            "shells_by_kind": shells_by_kind,
            "height": max((s.elevation for s in model.stories), default=0.0),
            "plan": [lx, ly], "diaphragm": model.diaphragm,
            "base_fixity": model.base_fixity,
            "cases_not_run": list(getattr(model, "cases_not_run", []) or [])}


_SPLIT = re.compile(r";\s+(?=(?:combo|case|RS|TH|time|pushover|buckling|"
                    r"staged)\b)")


def _collect_warnings(results: dict, py_warnings: Iterable[str]) -> List[dict]:
    out: List[dict] = []
    seen = set()

    def add(source: str, msg) -> None:
        msg = str(msg).strip()
        if not msg or (source, msg) in seen:
            return
        seen.add((source, msg))
        out.append({"source": source, "message": msg})

    w = results.get("warning")
    if w:
        for part in _SPLIT.split(str(w)):
            add("run", part)
    for grp in ("cases", "combos", "rs_cases", "th_cases", "pushover",
                "staged"):
        for name, cd in (results.get(grp) or {}).items():
            if not isinstance(cd, dict):
                continue
            if cd.get("warning"):
                add(name, cd["warning"])
            for x in cd.get("warnings") or []:
                add(name, x)
    for x in py_warnings:
        add("engine", x)
    return out


def build_run_log(model, results: dict, case_times: Dict[str, float],
                  total_s: float, py_warnings: Iterable[str] = (),
                  started: Optional[str] = None) -> Dict[str, Any]:
    """The audit record of one run (JSON-safe)::

        {"version", "started" (ISO-8601 UTC), "total_s", "model_name",
         "counts": {cases, combos, rs_cases, th_cases, modes},
         "cases": [{"name", "kind", "status", "time_s"}],
         "combos": [{"name", "status"}],
         "warnings": [{"source", "message"}]}

    ``kind`` from ``model.case_kinds()`` ("modal" for the modal case);
    ``time_s`` = None for a case not run (e.g. combos / skipped)."""
    status = results.get("case_status") or {}
    try:
        kinds = model.case_kinds()
    except Exception:                                  # pragma: no cover
        kinds = {}
    names = list(dict.fromkeys(list(kinds) + list(status) +
                               list(case_times)))
    cases = [{"name": n, "kind": kinds.get(n, "other"),
              "status": status.get(n, "finished" if n in case_times
                                   else "not_run"),
              "time_s": (round(float(case_times[n]), 6)
                         if n in case_times else None)} for n in names]
    combos = [{"name": n, "status": s}
              for n, s in (results.get("combo_status") or {}).items()]
    modal = results.get("modal") or {}
    return {"version": RUN_LOG_VERSION,
            "started": started or utc_now(),
            "total_s": round(float(total_s), 6),
            "model_name": results.get("model_name", model.name),
            "counts": {"cases": len(results.get("cases") or {}),
                       "combos": len(results.get("combos") or {}),
                       "rs_cases": len(results.get("rs_cases") or {}),
                       "th_cases": len(results.get("th_cases") or {}),
                       "modes": len(modal.get("periods") or [])},
            "cases": cases, "combos": combos,
            "warnings": _collect_warnings(results, py_warnings)}


DEFAULT_REPORT_TABLES = ("load_pattern_summary", "load_case_equilibrium",
                         "modal_periods", "modal_mass_ratios",
                         "base_reactions", "story_drifts", "story_forces")


def report_data(model, results: Optional[dict], context: Optional[dict],
                run_log: Optional[dict], tables=None,
                cases=None) -> Dict[str, Any]:
    """{model_info, run_log, results_available, tables: {key: table |
    {"error"}}} — tables computed from STORED results (never solves)."""
    from skyframe.core import tables as _tables
    keys = list(DEFAULT_REPORT_TABLES if tables is None else tables)
    out: Dict[str, Any] = {"model_info": model_info(model),
                           "run_log": run_log,
                           "results_available": results is not None,
                           "tables": {}}
    if results is None:
        return out
    for k in keys:
        if k not in _tables.TABLES:
            out["tables"][k] = {"error": f"unknown table {k!r}"}
            continue
        try:
            out["tables"][k] = _tables.compute_table(k, results, model,
                                                     context, cases=cases)
        except Exception as exc:                       # pragma: no cover
            out["tables"][k] = {"error": str(exc)}
    return out
