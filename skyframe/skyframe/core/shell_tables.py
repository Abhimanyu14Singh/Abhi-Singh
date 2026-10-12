"""Shell Forces / Shell Stresses analysis-results tables (A3).

Registered into :mod:`skyframe.core.tables` by a one-line hook
(:func:`register`), so the generic tables machinery (catalogue, case filter,
``/api/tables/<key>``, report data) serves them unchanged.  Values come from
:mod:`skyframe.core.shell_results`: element (centroid, Gauss-averaged,
UNaveraged) values in the ELEMENT local axes, one row per element and
output case, plus ``step_type`` "Max" / "Min" rows for RS cases and max/min
combos (ETABS "Step Type").  CONTRACT "Shell force contours and shell
stresses".

This module never imports :mod:`skyframe.core.tables` (it is imported BY it);
the table builders receive the tables ``_Ctx`` (``R``, ``model``,
``case_filter``, ``warnings``).
"""

from __future__ import annotations

from typing import Dict, List

from . import shell_results as sr

# new column quantities (base SI units) added to tables.QUANTITIES
NEW_QUANTITIES = {
    "line_force": "kN/m",          # membrane / transverse shear resultants
    "line_moment": "kN*m/m",       # plate moments
    "stress": "kPa",
    "angle_deg": "deg",            # principal directions
}

_CASE_TYPE = {"static": "LinStatic", "response_spectrum": "LinRespSpec",
              "rs_directional": "LinRespSpec", "combo": "Combination",
              "staged": "NonStaged",
              "time_history": "LinDirHist",           # Wave C TH shells
              "time_history_step": "LinDirHist"}


def _c(key: str, label: str, q: str) -> dict:
    return {"key": key, "label": label, "quantity": q}


_HEAD = [_c("story", "Story", "text"), _c("area", "Shell Object", "text"),
         _c("element", "Element", "id"), _c("case", "Output Case", "text"),
         _c("case_type", "Case Type", "text"),
         _c("step_type", "Step Type", "text")]


def _qcol(comp: str) -> dict:
    q = sr.COMPONENTS[comp][2]
    return _c(comp, comp, "angle_deg" if q == "angle" else q)


FORCE_COLS = _HEAD + [_qcol(k) for k in sr.FORCE_TABLE_COMPS]
STRESS_COLS = _HEAD + [_c("location", "Location", "text")] + \
    [_qcol(k) for k in sr.STRESS_TABLE_COMPS] + \
    [_c("S13", "S13Avg", "stress"), _c("S23", "S23Avg", "stress")]


def _sources(c) -> List[dict]:
    out = []
    for info in sr.list_sources(c.model, c.R):
        if c.case_filter is not None and info["name"] not in c.case_filter:
            continue
        out.append(info)
    return out


def _case_type(c, info: dict) -> str:
    if info["kind"] == "static":
        lc = c.model.cases.get(info["name"])
        geo = getattr(lc, "effective_geometric", "linear") if lc else "linear"
        return "LinStatic" if geo == "linear" else "NonStatic"
    return _CASE_TYPE.get(info["kind"], info["kind"])


def _emit(c, geo, info, comps, location, label, extra_rows=None) -> List[dict]:
    _i, hi, lo = sr.element_rows(c.model, c.R, info["name"], comps,
                                 location=location, geometry=geo)
    steps = [("Max", hi), ("Min", lo)] if info["bounded"] else [("", hi)]
    rows: List[dict] = []
    for step, vals in steps:
        for i, v in enumerate(vals):
            r = {"story": geo.story[i], "area": geo.region[i], "element": i,
                 "case": info["name"], "case_type": _case_type(c, info),
                 "step_type": step}
            if label is not None:
                r["location"] = label
            r.update(v)
            if extra_rows is not None:
                r.update(extra_rows[step][i])
            rows.append(r)
    return rows


def _t_shell_forces(c) -> List[dict]:
    if not c.R.get("shell_quads"):
        return []
    geo = sr.Geometry(c.model, c.R)
    rows: List[dict] = []
    for info in _sources(c):
        rows += _emit(c, geo, info, sr.FORCE_TABLE_COMPS, "top", None)
    rows.sort(key=lambda r: (r["case"], r["step_type"], r["element"]))
    return rows


def _t_shell_stresses(c) -> List[dict]:
    if not c.R.get("shell_quads"):
        return []
    geo = sr.Geometry(c.model, c.R)
    n_layers = max((len(geo.layers(i)) for i in range(geo.n)), default=0)
    if n_layers:
        c.warnings.append(
            "layered shells: layer stresses are recovered from the "
            "homogeneous elastic section strain of the linear analysis "
            "(approximation, see CONTRACT)")
    rows: List[dict] = []
    for info in _sources(c):
        _i, shi, slo = sr.element_rows(c.model, c.R, info["name"],
                                       ("S13", "S23"), geometry=geo)
        tr = {"Max": shi, "Min": slo, "": shi}
        locs = [("top", "Top"), ("bot", "Bottom")] + \
            [(f"layer:{k}", f"Layer {k + 1}") for k in range(n_layers)]
        for loc, lab in locs:
            part = _emit(c, geo, info, sr.STRESS_TABLE_COMPS, loc, lab, tr)
            if loc.startswith("layer"):
                k = int(loc[6:])
                part = [r for r in part if k < len(geo.layers(r["element"]))]
                for r in part:
                    la = geo.layers(r["element"])[k]
                    r["location"] = (f"Layer {k + 1} ({la['material']}, "
                                     f"{la['kind']})")
            rows += part
    order = {"Top": 0, "Bottom": 1}
    rows.sort(key=lambda r: (r["case"], r["step_type"], r["element"],
                             order.get(r["location"], 2), r["location"]))
    return rows


_G = "Analysis Results > Element Output > Area Output"
ENTRIES = [
    ("shell_forces", "Shell Forces", _G, FORCE_COLS, _t_shell_forces),
    ("shell_stresses", "Shell Stresses", _G, STRESS_COLS, _t_shell_stresses),
]


def register(table_list: list, table_map: Dict[str, tuple],
             quantities: Dict[str, str]) -> None:
    """Append the two tables to the core.tables registry (idempotent)."""
    for k, u in NEW_QUANTITIES.items():
        quantities.setdefault(k, u)
    for key, title, group, cols, fn in ENTRIES:
        if key in table_map:
            continue
        table_list.append((key, title, group, cols, fn))
        table_map[key] = (title, group, cols, fn)
