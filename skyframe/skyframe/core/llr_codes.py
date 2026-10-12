"""Live-load reduction by code, applied to frame (column) and pier output —
CONTRACT "Code spectra, design combinations, pattern types and live-load
reduction".

ETABS Design > Live Load Reduction Factors parity.  The model may carry
``BuildingModel.llr_settings`` (absent = None = ASCE 7-16 defaults when a
table asks); see :func:`normalize_settings` for the shape.  Analysis
results are NEVER modified: :func:`apply_to_results` returns reduced COPIES
of the column and pier forces, and two results tables
(``live_load_reduction_factors``, ``live_reduced_forces``) present them.

Reduction factor R (the multiplier on the reducible live load), with
``At`` the TOTAL tributary area supported (m^2, all floors at/above the
member), ``A1 = At / n`` the per-floor tributary area and ``n`` the number
of floors supported:

* ``asce7_16``  ASCE 7-16 §4.7.2 (also ASCE 7-22): ``0.25 + 4.57 /
  sqrt(KLL At)`` within [0.5 (n = 1) or 0.4 (n >= 2), 1]; KLL = 4.
* ``nbcc2020``  NBCC 2020 4.1.5.8: occupancies other than assembly /
  storage, ``B = At > 20 m^2``: ``0.3 + sqrt(9.8 / B)``; assembly /
  storage / manufacturing / retail / garages, ``A = At > 80 m^2``:
  ``0.5 + sqrt(20 / A)``; never above 1.
* ``ec1``       EN 1991-1-1 §6.3.1.2: ``alpha_A = 5/7 psi0 + A0/A1 <= 1``
  (A0 = 10 m^2; >= 0.6 for categories C and D) Eq. 6.1; ``alpha_n = (2 +
  (n - 2) psi0)/n`` for n > 2 storeys (Eq. 6.2, else 1).  ``method``
  alpha_n (columns, default) | alpha_A | min.
* ``is875``     IS 875 (Part 2):1987 §3.2.1 Table 3 (columns / walls /
  piers): 1 floor 0 %, 2: 10 %, 3: 20 %, 4: 30 %, 5-10: 40 %, > 10: 50 %.
* ``user``      a curve ``[[x, R], ...]`` over ``basis`` "area" (At, m^2)
  or "floors" (n); linear between points, end values beyond.

Tributary areas: columns use the existing half-bay rule of
:func:`skyframe.core.codes.live_load_reduction` (with its plan-area
fallback); piers use ``pier_areas[label]`` (m^2 per floor) when given,
else ``plan area / (columns + piers at that level)`` per floor.

Reducible live: static cases whose every pattern kind is in ``kinds``
(default ``["reducible_live"]``); when NO such case exists and ``kinds``
was not given, kind ``"live"`` cases are reduced instead (with a warning)
so pre-pattern-type models still work.  Linear superposition gives the
reduced value of any quantity ``q`` in a static case or single-valued
additive combo::

    q_red = q + sum_live (R - 1) * f_live * q_live

(``f_live`` = the case's / combo's flattened factor on that live case).
Envelope / RS / TH / nonlinear sources are not reduced (listed in
``skipped``).
"""

from __future__ import annotations

import copy
import math
import warnings
from typing import Any, Dict, List, Optional

LLR_CODES = ("asce7_16", "nbcc2020", "ec1", "is875", "user")
LLR_LABELS = {"asce7_16": "ASCE 7-16 / 7-22 §4.7", "nbcc2020": "NBCC 2020 "
              "4.1.5.8", "ec1": "EN 1991-1-1 §6.3.1.2",
              "is875": "IS 875 (Part 2) §3.2.1", "user": "User curve"}
IS875_TABLE = ((1, 1.0), (2, 0.9), (3, 0.8), (4, 0.7), (10, 0.6))

_DEFAULT_PARAMS = {
    "asce7_16": {"KLL": 4.0},
    "nbcc2020": {"occupancy": "general"},
    "ec1": {"psi0": 0.7, "category": "B", "method": "alpha_n", "A0": 10.0},
    "is875": {},
    "user": {"basis": "area", "curve": [[0.0, 1.0]]},
}
_STATION_KEYS = ("N", "V2", "V3", "T", "M2", "M3")


def _num(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v))


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #
def normalize_settings(s: Optional[dict]) -> dict:
    """Validated settings ``{code, params, kinds, kinds_given, apply_to,
    pier_areas}``; ValueError on bad input."""
    s = {} if s is None else s
    if not isinstance(s, dict):
        raise ValueError("llr_settings must be an object")
    bad = [k for k in s if k not in ("code", "params", "kinds", "apply_to",
                                     "pier_areas")]
    if bad:
        raise ValueError(f"llr_settings: unknown key(s) {bad}")
    code = s.get("code", "asce7_16")
    if code not in LLR_CODES:
        raise ValueError(f"llr_settings.code must be one of {LLR_CODES}")
    p = dict(_DEFAULT_PARAMS[code])
    given = s.get("params") or {}
    if not isinstance(given, dict):
        raise ValueError("llr_settings.params must be an object")
    for k in given:
        if k not in p:
            raise ValueError(f"llr_settings.params: unknown key {k!r} for "
                             f"{code}")
    p.update(given)
    if code == "asce7_16":
        if not (_num(p["KLL"]) and p["KLL"] > 0):
            raise ValueError("KLL must be > 0")
        p["KLL"] = float(p["KLL"])
    elif code == "nbcc2020":
        if p["occupancy"] not in ("general", "assembly"):
            raise ValueError("nbcc2020 occupancy must be general|assembly")
    elif code == "ec1":
        if not (_num(p["psi0"]) and 0.0 <= p["psi0"] <= 1.0):
            raise ValueError("psi0 must be in [0, 1]")
        if p["category"] not in ("A", "B", "C", "D", "E"):
            raise ValueError("ec1 category must be A-E")
        if p["method"] not in ("alpha_n", "alpha_A", "min"):
            raise ValueError("ec1 method must be alpha_n|alpha_A|min")
        if not (_num(p["A0"]) and p["A0"] > 0):
            raise ValueError("A0 must be > 0")
        p["psi0"], p["A0"] = float(p["psi0"]), float(p["A0"])
    elif code == "user":
        if p["basis"] not in ("area", "floors"):
            raise ValueError("user basis must be area|floors")
        cv = p["curve"]
        if (not isinstance(cv, (list, tuple)) or not cv
                or not all(isinstance(q, (list, tuple)) and len(q) == 2
                           and _num(q[0]) and _num(q[1]) for q in cv)):
            raise ValueError("user curve must be [[x, R], ...]")
        cv = [[float(a), float(b)] for a, b in cv]
        if any(b[0] <= a[0] for a, b in zip(cv, cv[1:])):
            raise ValueError("user curve x values must increase")
        if any(not (0.0 < r <= 1.0) for _, r in cv):
            raise ValueError("user curve factors must be in (0, 1]")
        p["curve"] = cv
    kinds = s.get("kinds")
    if kinds is not None and (not isinstance(kinds, list) or not kinds
                              or not all(isinstance(k, str) for k in kinds)):
        raise ValueError("llr_settings.kinds must be a list of pattern kinds")
    apply_to = s.get("apply_to", ["columns", "piers"])
    if (not isinstance(apply_to, list)
            or not set(apply_to) <= {"columns", "piers"}):
        raise ValueError("llr_settings.apply_to must list columns|piers")
    pa = s.get("pier_areas") or {}
    if not isinstance(pa, dict) or not all(_num(v) and v > 0
                                           for v in pa.values()):
        raise ValueError("llr_settings.pier_areas must map pier -> area > 0")
    return {"code": code, "params": p,
            "kinds": list(kinds) if kinds else ["reducible_live"],
            "kinds_given": kinds is not None, "apply_to": list(apply_to),
            "pier_areas": {k: float(v) for k, v in pa.items()}}


# --------------------------------------------------------------------------- #
# factor formulas (pure)
# --------------------------------------------------------------------------- #
def reduction_factor(code: str, params: dict, At: float, n: int) -> float:
    """R for a member supporting total tributary area ``At`` (m^2) over
    ``n`` floors."""
    n = max(int(n), 1)
    if code == "asce7_16":
        k = params.get("KLL", 4.0) * At
        R = 0.25 + 4.57 / math.sqrt(k) if k > 0 else 1.0
        return min(1.0, max(0.5 if n <= 1 else 0.4, R))
    if code == "nbcc2020":
        if params.get("occupancy", "general") == "assembly":
            return min(1.0, 0.5 + math.sqrt(20.0 / At)) if At > 80 else 1.0
        return min(1.0, 0.3 + math.sqrt(9.8 / At)) if At > 20 else 1.0
    if code == "ec1":
        psi0 = params.get("psi0", 0.7)
        A1 = At / n
        aA = min(1.0, 5.0 / 7.0 * psi0 + params.get("A0", 10.0) / A1) \
            if A1 > 0 else 1.0
        if params.get("category", "B") in ("C", "D"):
            aA = max(aA, 0.6)
        an = (2.0 + (n - 2) * psi0) / n if n > 2 else 1.0
        m = params.get("method", "alpha_n")
        return an if m == "alpha_n" else aA if m == "alpha_A" else \
            min(an, aA)
    if code == "is875":
        for nmax, r in IS875_TABLE:
            if n <= nmax:
                return r
        return 0.5
    if code == "user":
        x = At if params.get("basis", "area") == "area" else float(n)
        cv = params["curve"]
        if x <= cv[0][0]:
            return cv[0][1]
        if x >= cv[-1][0]:
            return cv[-1][1]
        for (x0, r0), (x1, r1) in zip(cv, cv[1:]):
            if x0 <= x <= x1:
                return r0 + (r1 - r0) * (x - x0) / (x1 - x0)
    raise ValueError(f"unknown live-load reduction code {code!r}")


# --------------------------------------------------------------------------- #
# per-element factors
# --------------------------------------------------------------------------- #
def _pier_labels(model, results: Optional[dict]) -> Dict[str, List[str]]:
    """pier label -> stories (from results when given, else the model)."""
    out: Dict[str, List[str]] = {}
    if results and results.get("piers"):
        for by_pier in results["piers"].values():
            for p, by_story in by_pier.items():
                lst = out.setdefault(p, [])
                for s in by_story:
                    if s not in lst:
                        lst.append(s)
        return out
    auto = getattr(model, "auto_pier_walls", False)
    for r in getattr(model, "shells", []) or []:
        if getattr(r, "kind", "") != "wall":
            continue
        lab = getattr(r, "pier", "") or (r.uid if auto else "")
        if lab:
            lst = out.setdefault(lab, [])
            if r.story and r.story not in lst:
                lst.append(r.story)
    return out


def element_factors(model, settings: Optional[dict] = None,
                    results: Optional[dict] = None) -> dict:
    """``{"code", "columns": {uid: {At, A1, n, R}}, "piers": {label:
    {story: {At, A1, n, R}}}, "warnings": [...]}``."""
    st = normalize_settings(settings)
    code, prm = st["code"], st["params"]
    warns: List[str] = []
    cols: Dict[str, dict] = {}
    if "columns" in st["apply_to"]:
        from .codes import live_load_reduction
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            geo = live_load_reduction(model)
        warns += [str(w.message) for w in caught]
        for uid, g in geo.items():
            n = int(round(g["n_stories"]))
            At = float(g["At"])
            cols[uid] = {"At": At, "A1": At / max(n, 1), "n": n,
                         "R": float(reduction_factor(code, prm, At, n))}
    piers: Dict[str, Dict[str, dict]] = {}
    if "piers" in st["apply_to"]:
        labels = _pier_labels(model, results)
        if labels:
            elev = {s.name: s.elevation for s in model.stories}
            lx, ly = model.plan_extents()
            ncol_at: Dict[float, int] = {}
            for m in model.members:
                if m.kind == "column":
                    z = round(max(m.pi[2], m.pj[2]), 6)
                    ncol_at[z] = ncol_at.get(z, 0) + 1
            for lab, stories in labels.items():
                for s in stories:
                    if s not in elev:
                        continue
                    z = elev[s]
                    n = max(sum(1 for e in elev.values()
                                if e >= z - 1e-6), 1)
                    if lab in st["pier_areas"]:
                        A1 = st["pier_areas"][lab]
                    else:
                        npier = sum(1 for ss in labels.values() if s in ss)
                        A1 = lx * ly / max(ncol_at.get(round(z, 6), 0)
                                           + npier, 1)
                    At = A1 * n
                    piers.setdefault(lab, {})[s] = {
                        "At": At, "A1": A1, "n": n,
                        "R": float(reduction_factor(code, prm, At, n))}
    return {"code": code, "columns": cols, "piers": piers,
            "warnings": warns}


def reducible_cases(model, settings: Optional[dict] = None):
    """(case names, warnings): static cases all of whose patterns are of a
    reducible kind (see the module doc for the ``live`` fallback)."""
    st = normalize_settings(settings)

    def pick(kinds):
        out = []
        for cname, case in model.cases.items():
            pats = [model.patterns.get(p) for p in case.patterns]
            if pats and all(p is not None and p.kind in kinds
                            for p in pats):
                out.append(cname)
        return out
    names = pick(st["kinds"])
    warns: List[str] = []
    if not names and not st["kinds_given"]:
        names = pick(["live"])
        if names:
            warns.append("no Reducible Live load patterns: the Live-type "
                         f"cases {names} are reduced instead")
    return names, warns


# --------------------------------------------------------------------------- #
# application to results
# --------------------------------------------------------------------------- #
def _live_factors(model, name: str, kind: str, live: List[str]
                  ) -> Optional[Dict[str, float]]:
    """{live case: factor} for a reducible source, or None (not linear)."""
    if kind == "cases":
        if name not in model.cases:
            return None
        return {name: 1.0} if name in live else {}
    cb = model.combos.get(name)
    if cb is None or cb.combo_type != "add":
        return None
    from .combos_ext import is_single_valued, leaf_factors
    if all(c in model.cases for c in cb.cases):
        lf = {c: float(f) for c, f in cb.cases.items()}
    elif is_single_valued(model, name):
        lf = leaf_factors(model, name)
    else:
        return None
    return {c: f for c, f in lf.items() if c in live and f != 0.0}


def apply_to_results(results, model, settings: Optional[dict] = None
                     ) -> dict:
    """Reduced copies of the column and pier forces.

    Returns ``{"code", "live_cases", "factors": {columns, piers},
    "cases": {name: {"member_forces": {uid: [12]}, "member_stations":
    {uid: {x, N, V2, V3, T, M2, M3}}}}, "combos": {...same...}, "piers":
    {source: {label: {story: {P, V, M}}}}, "live_factor": {source: {live
    case: f}}, "skipped": [source], "warnings": [...]}`` — only the
    elements with R != 1 in sources with a live share are listed."""
    d = results.to_dict() if hasattr(results, "to_dict") else results
    if not isinstance(d, dict):
        raise TypeError("results must be a results object or a results dict")
    live, warns = reducible_cases(model, settings)
    fac = element_factors(model, settings, d)
    warns += fac["warnings"]
    out: Dict[str, Any] = {"code": fac["code"], "live_cases": live,
                           "factors": {"columns": fac["columns"],
                                       "piers": fac["piers"]},
                           "cases": {}, "combos": {}, "piers": {},
                           "live_factor": {}, "skipped": [],
                           "warnings": warns}
    cases_res = d.get("cases") or {}
    missing = [c for c in live if c not in cases_res]
    if missing:
        warns.append(f"live case(s) {missing} were not run; not reduced")
        live = [c for c in live if c in cases_res]
    if not live:
        return out
    piers_res = d.get("piers") or {}
    for kind in ("cases", "combos"):
        for name, block in (d.get(kind) or {}).items():
            lf = _live_factors(model, name, kind, live)
            if lf is None:
                out["skipped"].append(name)
                continue
            if not lf:
                continue
            out["live_factor"][name] = lf
            mf_out, st_out = {}, {}
            for uid, info in fac["columns"].items():
                if info["R"] == 1.0:
                    continue
                mf = (block.get("member_forces") or {}).get(uid)
                if mf:
                    v = [float(x) for x in mf]
                    for lc, f in lf.items():
                        ml = (cases_res[lc].get("member_forces") or {}
                              ).get(uid)
                        if ml:
                            sc = (info["R"] - 1.0) * f
                            v = [a + sc * float(b) for a, b in zip(v, ml)]
                    mf_out[uid] = v
                stn = (block.get("member_stations") or {}).get(uid)
                if stn:
                    s2 = copy.deepcopy(stn)
                    for lc, f in lf.items():
                        sl = (cases_res[lc].get("member_stations") or {}
                              ).get(uid)
                        if not sl:
                            continue
                        sc = (info["R"] - 1.0) * f
                        for k in _STATION_KEYS:
                            if k in s2 and k in sl:
                                s2[k] = [float(a) + sc * float(b)
                                         for a, b in zip(s2[k], sl[k])]
                    st_out[uid] = s2
            out[kind][name] = {"member_forces": mf_out,
                               "member_stations": st_out}
            src = piers_res.get(name)
            if src:
                pr: Dict[str, Dict[str, dict]] = {}
                for lab, by_story in src.items():
                    for s, v in by_story.items():
                        info = fac["piers"].get(lab, {}).get(s)
                        if info is None or info["R"] == 1.0:
                            continue
                        nv = {k: float(x) for k, x in v.items()}
                        for lc, f in lf.items():
                            lv = ((piers_res.get(lc) or {}).get(lab) or {}
                                  ).get(s)
                            if lv:
                                sc = (info["R"] - 1.0) * f
                                for k in nv:
                                    if k in lv:
                                        nv[k] += sc * float(lv[k])
                        pr.setdefault(lab, {})[s] = nv
                if pr:
                    out["piers"][name] = pr
    return out


# --------------------------------------------------------------------------- #
# results tables (hooked into core.tables)
# --------------------------------------------------------------------------- #
_G = "Analysis Results > Frame Output > Live Load Reduction"


def _cols():
    def c(k, label, q):
        return {"key": k, "label": label, "quantity": q}
    fac = [c("story", "Story", "text"), c("element", "Element", "text"),
           c("name", "Unique Name", "text"), c("code", "Code", "text"),
           c("at", "Tributary Area", "area"), c("n", "Floors", "id"),
           c("rll", "RLL Factor", "ratio")]
    frc = [c("story", "Story", "text"), c("element", "Element", "text"),
           c("name", "Unique Name", "text"), c("case", "Output Case", "text"),
           c("case_type", "Case Type", "text"), c("station", "Station",
                                                   "length"),
           c("rll", "RLL Factor", "ratio"), c("P", "P", "force"),
           c("V2", "V2", "force"), c("V3", "V3", "force"),
           c("T", "T", "moment"), c("M2", "M2", "moment"),
           c("M3", "M3", "moment")]
    return fac, frc


def _story_of(model, uid):
    m = next((m for m in model.members if m.uid == uid), None)
    return (m.story or "") if m is not None else ""


def _t_factors(c) -> List[dict]:
    model = c.model
    st = getattr(model, "llr_settings", None)
    f = element_factors(model, st, c.R)
    c.warnings.extend(f["warnings"])
    rows = []
    lab = LLR_LABELS[f["code"]]
    for uid, i in f["columns"].items():
        rows.append({"story": _story_of(model, uid), "element": "Column",
                     "name": uid, "code": lab, "at": i["At"], "n": i["n"],
                     "rll": i["R"]})
    for p, by in f["piers"].items():
        for s, i in by.items():
            rows.append({"story": s, "element": "Pier", "name": p,
                         "code": lab, "at": i["At"], "n": i["n"],
                         "rll": i["R"]})
    return rows


def _t_forces(c) -> List[dict]:
    model = c.model
    red = apply_to_results(c.R, model, getattr(model, "llr_settings", None))
    c.warnings.extend(red["warnings"])
    fc = red["factors"]["columns"]
    fp = red["factors"]["piers"]
    rows = []
    for name, ctype, cd in c.sources():
        kind = "cases" if name in (c.R.get("cases") or {}) else "combos"
        blk = red[kind].get(name)
        if blk is None:
            continue
        for uid, info in fc.items():
            st = blk["member_stations"].get(uid) or \
                (cd.get("member_stations") or {}).get(uid)
            if not st or not st.get("x"):
                continue
            for idx in (0, len(st["x"]) - 1):
                rows.append({"story": _story_of(model, uid),
                             "element": "Column", "name": uid,
                             "case": name, "case_type": ctype,
                             "station": float(st["x"][idx]),
                             "rll": info["R"],
                             "P": float(st["N"][idx]),
                             "V2": float(st["V2"][idx]),
                             "V3": float(st["V3"][idx]),
                             "T": float(st["T"][idx]),
                             "M2": float(st["M2"][idx]),
                             "M3": float(st["M3"][idx])})
        src = (c.R.get("piers") or {}).get(name) or {}
        rp = red["piers"].get(name) or {}
        for lab, by in src.items():
            for s, v in by.items():
                vv = (rp.get(lab) or {}).get(s, v)
                rows.append({"story": s, "element": "Pier", "name": lab,
                             "case": name, "case_type": ctype,
                             "station": None,
                             "rll": (fp.get(lab) or {}).get(s, {}).get("R",
                                                                       1.0),
                             "P": float(vv.get("P", 0.0)),
                             "V2": float(vv.get("V", 0.0)), "V3": None,
                             "T": None, "M2": None,
                             "M3": float(vv.get("M", 0.0))})
    return rows


def table_entries():
    """(key, title, group, columns, fn) rows appended to core.tables."""
    fac, frc = _cols()
    return [("live_load_reduction_factors", "Live Load Reduction Factors",
             _G, fac, _t_factors),
            ("live_reduced_forces", "Column And Pier Forces (Live Load "
             "Reduced)", _G, frc, _t_forces)]
