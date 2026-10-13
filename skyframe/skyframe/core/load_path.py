"""Load-path explainer (Tools > Explain Result...).

For one result quantity (the *target*) of one load case or combination,
break the value down into its contributions:

* linear static case  -> per load pattern (``scale x pattern response``);
  a pattern's response comes from a stored single-pattern linear case
  (``{pattern: s}``, divided by ``s``) when the results hold one, else it
  is RE-SOLVED on the same model (one linear solve per pattern on the
  reused elastic domain; superposition is exact for linear cases);
* load combination   -> per member case with its factor, following the
  interval rules of CONTRACT "Load combinations: RS/TH/nested members"
  (nested Linear-Add combos are flattened into leaves); envelope / max-min
  combos report which member GOVERNS the max and the min; static leaves
  of additive combos can be expanded per pattern (factor = combo factor x
  case scale);
* response spectrum  -> per mode: the signed modal value ``r_i`` (the same
  per-mode static solve the engine uses), its share ``c_i = r_i sum_j
  rho_ij r_j`` of ``R^2`` (CQC; ``rho = I`` for SRSS) and the cross
  terms ``sum_{i!=j} rho_ij r_i r_j``;
* time history       -> the governing step (time) of the recorded series
  and the next-largest steps.

Nonlinear cases (P-Delta / large-displacement static cases, models with
tension/compression-only members, gap/isolator links or compression-only
springs, pushover, staged, nonlinear static) are never re-solved: the
result carries ``supported: false`` and an explanatory ``message``.

Targets (``target`` dict):

======================  ============================================  ======
kind                    identifiers                                   comps
======================  ============================================  ======
``reaction``            ``joint`` (support node tag)                  FX..MZ
``joint_disp``          ``joint`` (node tag)                          UX..RZ
``member_force``        ``member`` (uid), ``station`` 0..1 (fraction  P V2 V3
                        of length, nearest output station)            T M2 M3
``story``               ``story`` (name)                              ux uy
                                                                      drift_x
                                                                      drift_y
                                                                      shear_x
                                                                      shear_y
``base``                --                                            FX..MZ
``pier``                ``pier`` (label), ``story``                   P V M
======================  ============================================  ======

CONTRACT "Load-path explainer".
"""

from __future__ import annotations

import copy
import math
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

COMPONENTS = {
    "reaction": ("FX", "FY", "FZ", "MX", "MY", "MZ"),
    "joint_disp": ("UX", "UY", "UZ", "RX", "RY", "RZ"),
    "member_force": ("P", "V2", "V3", "T", "M2", "M3"),
    "story": ("ux", "uy", "drift_x", "drift_y", "shear_x", "shear_y"),
    "base": ("FX", "FY", "FZ", "MX", "MY", "MZ"),
    "pier": ("P", "V", "M"),
}
TARGET_KINDS = tuple(COMPONENTS)
_STATION_KEY = {"P": "N", "V2": "V2", "V3": "V3", "T": "T", "M2": "M2",
                "M3": "M3"}
_TMP_PREFIX = "__lp__"
MAX_CACHE = 6
_CACHE: "OrderedDict[tuple, Any]" = OrderedDict()


# --------------------------------------------------------------------------- #
# targets
# --------------------------------------------------------------------------- #
def unit_kind(target: dict) -> str:
    k, c = target["kind"], target["component"]
    if k in ("reaction", "base"):
        return "force" if c.startswith("F") else "moment"
    if k == "joint_disp":
        return "disp" if c.startswith("U") else "rotation"
    if k == "member_force":
        return "force" if c in ("P", "V2", "V3") else "moment"
    if k == "story":
        return ("disp" if c in ("ux", "uy") else
                "force" if c.startswith("shear") else "none")
    if k == "pier":
        return "moment" if c == "M" else "force"
    return "none"


def normalize_target(t: Any) -> dict:
    """Validate and normalise a target dict (ValueError on bad input)."""
    if not isinstance(t, dict):
        raise ValueError("target must be an object")
    kind = str(t.get("kind", ""))
    if kind not in COMPONENTS:
        raise ValueError(f"target kind must be one of {TARGET_KINDS}")
    comp = str(t.get("component", ""))
    comps = COMPONENTS[kind]
    match = next((c for c in comps if c.lower() == comp.lower()), None)
    if match is None:
        raise ValueError(f"{kind} component must be one of {comps}")
    out = {"kind": kind, "component": match}
    if kind in ("reaction", "joint_disp"):
        j = t.get("joint")
        if j is None or str(j).strip() == "":
            raise ValueError(f"{kind} target needs 'joint' (node tag)")
        out["joint"] = str(j).strip()
    elif kind == "member_force":
        if not t.get("member"):
            raise ValueError("member_force target needs 'member' (uid)")
        out["member"] = str(t["member"])
        st = t.get("station", 0.0)
        if isinstance(st, str):
            st = {"i": 0.0, "j": 1.0}.get(st.strip().lower(), st)
        try:
            st = float(st)
        except (TypeError, ValueError):
            raise ValueError("station must be a number 0..1, 'i' or 'j'")
        if not 0.0 <= st <= 1.0:
            raise ValueError("station must be in [0, 1]")
        out["station"] = st
    elif kind == "story":
        if not t.get("story"):
            raise ValueError("story target needs 'story'")
        out["story"] = str(t["story"])
    elif kind == "pier":
        if not t.get("pier") or not t.get("story"):
            raise ValueError("pier target needs 'pier' and 'story'")
        out["pier"] = str(t["pier"])
        out["story"] = str(t["story"])
    return out


def target_label(t: dict) -> str:
    k, c = t["kind"], t["component"]
    if k == "reaction":
        return f"Reaction {c} at joint {t['joint']}"
    if k == "joint_disp":
        return f"Displacement {c} of joint {t['joint']}"
    if k == "member_force":
        st = t["station"]
        where = ("end I" if st == 0.0 else "end J" if st == 1.0
                 else f"station {st:g}·L")
        return f"Frame {t['member']} {c} at {where}"
    if k == "story":
        return f"Story {t['story']} {c.replace('_', ' ')}"
    if k == "base":
        return f"Base reaction {c}"
    return f"Pier {t['pier']} {c} at {t['story']}"


def _station_index(xs: List[float], frac: float) -> int:
    if not xs:
        return 0
    L = xs[-1] - xs[0]
    x = xs[0] + frac * L
    return min(range(len(xs)), key=lambda i: abs(xs[i] - x))


def extract(part: dict, t: dict) -> Optional[float]:
    """Value of target ``t`` in one result part ``{"case": <case dict>,
    "piers": {pier: {story: {P, V, M}}}}`` (None when absent)."""
    cd = part.get("case") or {}
    k, c = t["kind"], t["component"]
    try:
        if k == "reaction":
            v = cd.get("reactions", {}).get(t["joint"])
            return None if v is None else float(
                v[COMPONENTS[k].index(c)])
        if k == "joint_disp":
            v = cd.get("node_disp", {}).get(t["joint"])
            return None if v is None else float(
                v[COMPONENTS[k].index(c)])
        if k == "base":
            v = cd.get("base", {}).get(c)
            return None if v is None else float(v)
        if k == "story":
            v = cd.get("story", {}).get(t["story"], {}).get(c)
            return None if v is None else float(v)
        if k == "member_force":
            st = cd.get("member_stations", {}).get(t["member"])
            if st and st.get(_STATION_KEY[c]):
                i = _station_index(st.get("x") or [], t["station"])
                return float(st[_STATION_KEY[c]][i])
            ef = cd.get("member_forces", {}).get(t["member"])
            if ef and t["station"] in (0.0, 1.0):
                off = 0 if t["station"] == 0.0 else 6
                return float(ef[off + COMPONENTS[k].index(c)])
            return None
        if k == "pier":
            v = (part.get("piers") or {}).get(t["pier"], {}).get(
                t["story"], {}).get(c)
            return None if v is None else float(v)
    except (IndexError, TypeError, ValueError, KeyError):
        return None
    return None


def station_x(results_case: dict, t: dict) -> Optional[float]:
    if t["kind"] != "member_force":
        return None
    st = (results_case or {}).get("member_stations", {}).get(t["member"])
    if not st or not st.get("x"):
        return None
    return float(st["x"][_station_index(st["x"], t["station"])])


def _part(results: dict, block: str, name: str) -> Optional[dict]:
    cd = (results.get(block) or {}).get(name)
    if not isinstance(cd, dict):
        return None
    return {"case": cd, "piers": (results.get("piers") or {}).get(name, {})}


def _min_part(part: Optional[dict], results: dict, name: str
              ) -> Optional[dict]:
    if part is None or not isinstance(part["case"].get("min"), dict):
        return None
    pm = ((results.get("piers") or {}).get(name + "::min")
          or (results.get("piers") or {}).get(f"{name} (min)") or {})
    return {"case": part["case"]["min"], "piers": pm}


# --------------------------------------------------------------------------- #
# nonlinearity checks
# --------------------------------------------------------------------------- #
def nonlinear_reason(model, case_name: Optional[str] = None
                     ) -> Optional[str]:
    """Why superposition / re-solving is not valid, or None."""
    if case_name is not None and case_name in model.cases:
        geo = model.cases[case_name].effective_geometric
        if geo != "linear":
            return (f"case {case_name!r} is solved with {geo} geometric "
                    "nonlinearity; its pattern responses do not superpose")
    if any(getattr(m, "axial_limit", "both") != "both"
           for m in model.members):
        return ("the model has tension/compression-only members, so "
                "static cases are solved nonlinearly")
    try:
        from skyframe.engine.opensees_engine import \
            NONLINEAR_STATIC_LINK_TYPES
    except ImportError:                              # pragma: no cover
        NONLINEAR_STATIC_LINK_TYPES = ()
    if any(getattr(lk, "link_type", "elastic") in NONLINEAR_STATIC_LINK_TYPES
           for lk in model.links):
        return ("the model has gap / hook / isolator links, so static "
                "cases are solved nonlinearly")
    if any(ls.compression_only and ls.kz > 0.0
           for ls in getattr(model, "line_springs", [])):
        return ("the model has compression-only springs, so static cases "
                "are solved nonlinearly")
    if any((getattr(r, "area_spring", None) or {}).get("compression_only")
           for r in model.shells):
        return ("the model has compression-only area springs, so static "
                "cases are solved nonlinearly")
    try:
        from skyframe.core import springprops as _sp
        if _sp.any_nonlinear(model):
            return ("the model has nonlinear spring properties, so static "
                    "cases are solved nonlinearly")
    except Exception:                                # pragma: no cover
        pass
    return None


# --------------------------------------------------------------------------- #
# per-pattern parts (stored or re-solved)
# --------------------------------------------------------------------------- #
def _engine_cls():
    from skyframe.engine.opensees_engine import OpenSeesEngine
    return OpenSeesEngine


def _stored_single(model, results: dict, pattern: str
                   ) -> Optional[Tuple[str, float]]:
    """A stored LINEAR single-pattern case ``{pattern: s}`` -> (name, s)."""
    for name, lc in model.cases.items():
        pats = {p: f for p, f in lc.patterns.items() if f}
        if (len(pats) == 1 and pattern in pats
                and lc.effective_geometric == "linear"
                and name in (results.get("cases") or {})):
            return name, float(pats[pattern])
    return None


def _scaled(part: dict, s: float) -> dict:
    """``part / s`` lazily: values are divided at extraction time."""
    return {"case": part["case"], "piers": part["piers"], "div": s}


def pattern_parts(model, results: dict, patterns: List[str],
                  need_piers: bool = False, cache_key=None
                  ) -> Dict[str, Tuple[dict, str]]:
    """``{pattern: (part, source)}`` with source ``"stored:<case>"`` or
    ``"re-solved"``.  Re-solves only the patterns without a stored
    single-pattern linear case (one engine, reused elastic domain)."""
    out: Dict[str, Tuple[dict, str]] = {}
    missing = []
    for p in patterns:
        hit = _stored_single(model, results, p)
        if hit is not None and not need_piers or (
                hit is not None and need_piers
                and hit[0] in (results.get("piers") or {})):
            part = _part(results, "cases", hit[0])
            out[p] = (_scaled(part, hit[1]) if hit[1] != 1.0 else part,
                      f"stored:{hit[0]}")
        else:
            missing.append(p)
    if not missing:
        return out
    ck = (cache_key, "patterns", tuple(missing), need_piers)
    if cache_key is not None and ck in _CACHE:
        _CACHE.move_to_end(ck)
        out.update(_CACHE[ck])
        return out
    from skyframe.core.model import BuildingModel, LoadCase
    tmp = BuildingModel.from_dict(model.to_dict())
    names = {}
    for i, p in enumerate(missing):
        n = f"{_TMP_PREFIX}{i}"
        while n in tmp.cases:
            n += "_"
        tmp.cases[n] = LoadCase(n, {p: 1.0})
        names[p] = n
    eng = _engine_cls()(tmp)
    solved = {}
    for p, n in names.items():
        cr = eng.run_static(n)
        piers = {}
        if need_piers:
            try:
                piers = eng._compute_piers(eng._asm, {n: cr}).get(n, {})
            except Exception:                        # pragma: no cover
                piers = {}
        solved[p] = ({"case": cr.to_dict(), "piers": piers}, "re-solved")
    if cache_key is not None:
        _CACHE[ck] = solved
        while len(_CACHE) > MAX_CACHE:
            _CACHE.popitem(last=False)
    out.update(solved)
    return out


def _val(part: dict, t: dict) -> Optional[float]:
    v = extract(part, t)
    if v is None:
        return None
    return v / part["div"] if part.get("div") else v


# --------------------------------------------------------------------------- #
# explainers
# --------------------------------------------------------------------------- #
def _rank(rows: List[dict], key: str = "contribution") -> List[dict]:
    rows.sort(key=lambda r: -abs(r.get(key) or 0.0))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def _shares(rows: List[dict], total: Optional[float],
            key: str = "contribution") -> None:
    den = sum(abs(r.get(key) or 0.0) for r in rows)
    for r in rows:
        v = r.get(key) or 0.0
        r["share"] = (v / total) if total else None
        r["abs_share"] = abs(v) / den if den else 0.0


def explain_static(model, results: dict, t: dict, name: str,
                   cache_key=None) -> dict:
    lc = model.cases[name]
    out = {"case_kind": "static", "method": "superposition"}
    stored = _part(results, "cases", name)
    total = extract(stored, t) if stored else None
    out["value"] = total
    why = nonlinear_reason(model, name)
    if why:
        out.update(supported=False, method="nonlinear",
                   message=("No pattern breakdown: " + why + ". Define a "
                            "linear case per pattern to compare, or run "
                            "the patterns as separate nonlinear cases."))
        return out
    pats = [p for p, f in lc.patterns.items()]
    parts = pattern_parts(model, results, pats, need_piers=(
        t["kind"] == "pier"), cache_key=cache_key)
    rows = []
    for p, f in lc.patterns.items():
        part, src = parts[p]
        v = _val(part, t)
        rows.append({"kind": "pattern", "name": p, "factor": float(f),
                     "unit_value": v,
                     "contribution": None if v is None else f * v,
                     "source": src,
                     "link": {"type": "pattern", "pattern": p,
                              "case": src.split(":", 1)[1]
                              if src.startswith("stored:") else None}})
    s = sum(r["contribution"] or 0.0 for r in rows)
    out["computed_total"] = s
    if total is None:
        total = s
        out["value"] = s
    _shares(rows, total)
    out["rows"] = _rank(rows)
    out["check"] = _check(total, s)
    nre = sum(1 for r in rows if r["source"] == "re-solved")
    out["notes"] = [f"{len(rows)} pattern(s); {nre} re-solved by "
                    "superposition (linear case), "
                    f"{len(rows) - nre} from stored single-pattern cases."]
    if t["kind"] == "story" and t["component"].startswith("shear"):
        out["notes"].append("Story shear is the applied lateral force at "
                            "and above the story (engine definition).")
    return out


def _check(total, s) -> dict:
    if total is None:
        return {"stored": None, "sum": s, "rel_err": None, "ok": True}
    err = abs(s - total) / max(abs(total), 1e-12 * max(1.0, abs(s)), 1e-30)
    if abs(total) < 1e-9 and abs(s) < 1e-9:
        err = 0.0
    return {"stored": total, "sum": s, "rel_err": err, "ok": err < 1e-6}


def _flatten_add(model, name: str, factor: float, out: List[Tuple[str, float]],
                 depth=0) -> None:
    from skyframe.core import combos_ext as _cx
    cb = model.combos[name]
    for m, f in cb.cases.items():
        kind = _cx.member_kind(model, m)
        if kind == "combo" and model.combos[m].combo_type == "add" \
                and depth < 32:
            _flatten_add(model, m, factor * f, out, depth + 1)
        else:
            out.append((m, factor * f))


def _th_interval(results: dict, name: str, t: dict, model
                 ) -> Optional[Tuple[float, float]]:
    th = (results.get("th_cases") or {}).get(name)
    if not isinstance(th, dict):
        return None
    ser = th_series(th, t, model)
    if ser is not None:
        return (min(ser), max(ser)) if ser else (0.0, 0.0)
    if t["kind"] == "story" and t["component"].startswith("shear"):
        pk = th.get("peaks", {}).get("story", {}).get(t["story"], {}).get(
            t["component"])
        if pk is not None:
            return (-abs(pk), abs(pk))
    return None


def th_series(th: dict, t: dict, model=None) -> Optional[List[float]]:
    """The recorded signed series of ``t`` in a TH result, or None."""
    k, c = t["kind"], t["component"]
    if k == "base" and c in ("FX", "FY"):
        return list(th.get("base_" + c) or [])
    if k == "story" and c in ("ux", "uy"):
        s = (th.get("story_" + c) or {}).get(t["story"])
        return None if s is None else list(s)
    if k == "story" and c in ("drift_x", "drift_y") and model is not None:
        comp = "ux" if c == "drift_x" else "uy"
        names = [s.name for s in model.stories]
        if t["story"] not in names:
            return None
        i = names.index(t["story"])
        cur = (th.get("story_" + comp) or {}).get(t["story"])
        if cur is None:
            return None
        prev = ((th.get("story_" + comp) or {}).get(names[i - 1])
                if i > 0 else None) or [0.0] * len(cur)
        h = model.stories[i].height or 1.0
        return [(a - b) / h for a, b in zip(cur, prev)]
    return None


def explain_combo(model, results: dict, t: dict, name: str,
                  expand: bool = True, cache_key=None) -> dict:
    from skyframe.core import combos_ext as _cx
    cb = model.combos[name]
    ctype = cb.combo_type
    out = {"case_kind": "combo", "combo_type": ctype,
           "method": f"combo-{ctype}"}
    stored = _part(results, "combos", name)
    smax = extract(stored, t) if stored else None
    smin_part = _min_part(stored, results, name)
    smin = extract(smin_part, t) if smin_part else None
    leaves: List[Tuple[str, float]] = []
    if ctype == "add":
        _flatten_add(model, name, 1.0, leaves)
    else:
        leaves = [(m, float(f)) for m, f in cb.cases.items()]
    rows = []
    notes = []
    single = True
    for m, f in leaves:
        kind = _cx.member_kind(model, m)
        row = {"kind": "case", "case_kind": kind, "name": m,
               "factor": float(f), "link": {"type": "case", "case": m}}
        lo = hi = None
        if kind == "static":
            v = extract(_part(results, "cases", m) or {}, t)
            if v is not None:
                lo = hi = f * v
                row["unit_value"] = v
            if expand and ctype == "add" and v is not None:
                row["children"] = _pattern_children(
                    model, results, t, m, f, cache_key, notes)
        elif kind in ("response_spectrum", "rs_directional"):
            single = False
            v = extract(_part(results, "rs_cases", m) or {}, t)
            if v is not None:
                lo, hi = -abs(f) * abs(v), abs(f) * abs(v)
                row["unit_value"] = v
                row["signless"] = True
        elif kind == "time_history":
            single = False
            iv = _th_interval(results, m, t, model)
            if iv is not None:
                a, b = f * iv[0], f * iv[1]
                lo, hi = min(a, b), max(a, b)
        elif kind == "combo":
            p = _part(results, "combos", m)
            vmax = extract(p, t) if p else None
            pm = _min_part(p, results, m)
            vmin = extract(pm, t) if pm else None
            if vmax is not None:
                if vmin is None:
                    lo = hi = f * vmax
                    row["unit_value"] = vmax
                else:
                    single = False
                    a, b = f * vmax, f * vmin
                    lo, hi = min(a, b), max(a, b)
        elif kind == "staged":
            st = (results.get("staged") or {}).get(m) or {}
            cd = st.get("case") if isinstance(st.get("case"), dict) else None
            v = extract({"case": cd}, t) if cd else None
            if v is not None:
                lo = hi = f * v
                row["unit_value"] = v
        if lo is None:
            row["available"] = False
            notes.append(f"{m}: this quantity is not available in its "
                         "results (not run, or not recorded for this case "
                         "type).")
        row["lo"], row["hi"] = lo, hi
        rows.append(row)
    avail = [r for r in rows if r["lo"] is not None]
    if ctype == "add":
        cmax = sum(r["hi"] for r in avail)
        cmin = sum(r["lo"] for r in avail)
        for r in avail:
            r["contribution"] = r["hi"]
            r["contribution_min"] = r["lo"]
    elif ctype == "envelope":
        cmax = max((r["hi"] for r in avail), default=0.0)
        cmin = min((r["lo"] for r in avail), default=0.0)
        gmax = max(avail, key=lambda r: r["hi"], default=None)
        gmin = min(avail, key=lambda r: r["lo"], default=None)
        for r in avail:
            r["contribution"] = r["hi"]
            r["contribution_min"] = r["lo"]
            r["governs_max"] = r is gmax
            r["governs_min"] = r is gmin
        single = False
    elif ctype == "abs":
        for r in avail:
            r["contribution"] = max(abs(r["hi"]), abs(r["lo"]))
        cmax = sum(r["contribution"] for r in avail)
        cmin = -cmax
        single = False
    elif ctype == "srss":
        for r in avail:
            r["contribution"] = max(abs(r["hi"]), abs(r["lo"]))
        cmax = math.sqrt(sum(r["contribution"] ** 2 for r in avail))
        cmin = -cmax
        for r in avail:
            r["srss_share"] = (r["contribution"] ** 2 / cmax ** 2
                               if cmax else 0.0)
        single = False
    else:                                   # range
        for r in avail:
            r["contribution"] = max(r["hi"], 0.0)
            r["contribution_min"] = min(r["lo"], 0.0)
        cmax = sum(r["contribution"] for r in avail)
        cmin = sum(r["contribution_min"] for r in avail)
        single = False
    single = single and smin is None
    out["single_valued"] = single
    out["value"] = smax if smax is not None else cmax
    out["value_min"] = None if single else (smin if smin is not None
                                            else cmin)
    out["computed_total"] = cmax
    out["computed_min"] = None if single else cmin
    out["check"] = _check(smax, cmax)
    if not single:
        out["check_min"] = _check(smin, cmin)
    _shares(rows, out["value"])
    out["rows"] = _rank(rows)
    if ctype == "envelope":
        g = next((r for r in rows if r.get("governs_max")), None)
        gm = next((r for r in rows if r.get("governs_min")), None)
        if g:
            notes.insert(0, f"Max governed by {g['name']} "
                            f"(factor {g['factor']:g}).")
        if gm:
            notes.insert(1 if g else 0, f"Min governed by {gm['name']} "
                         f"(factor {gm['factor']:g}).")
    if any(r.get("signless") for r in rows):
        notes.append("Response-spectrum members carry no sign: they add "
                     "+|f|·R to the max and −|f|·R to the min (ETABS rule).")
    out["notes"] = notes
    return out


def _pattern_children(model, results, t, case, f, cache_key, notes):
    if nonlinear_reason(model, case):
        return []
    lc = model.cases[case]
    try:
        parts = pattern_parts(model, results, list(lc.patterns),
                              need_piers=t["kind"] == "pier",
                              cache_key=cache_key)
    except Exception as exc:                         # pragma: no cover
        notes.append(f"{case}: pattern breakdown failed ({exc})")
        return []
    ch = []
    for p, s in lc.patterns.items():
        part, src = parts[p]
        v = _val(part, t)
        ch.append({"kind": "pattern", "name": p, "factor": float(f * s),
                   "unit_value": v,
                   "contribution": None if v is None else f * s * v,
                   "source": src,
                   "link": {"type": "pattern", "pattern": p}})
    ch.sort(key=lambda r: -abs(r["contribution"] or 0.0))
    return ch


def explain_rs(model, results: dict, t: dict, name: str,
               cache_key=None) -> dict:
    rs = model.rs_cases[name]
    stored = _part(results, "rs_cases", name)
    out = {"case_kind": "response_spectrum", "method": "modal",
           "value": extract(stored, t) if stored else None,
           "combination": getattr(rs, "combo_method", "CQC")}
    from skyframe.engine import modal_cases as _mcx          # noqa: F401
    from skyframe.engine import modalcombo as _mc
    from skyframe.engine import opensees_engine as _oe
    from skyframe.engine import rs_ext as _rsx
    reason = None
    try:
        from skyframe.engine import modal_damping as _mdx
    except ImportError:                              # pragma: no cover
        _mdx = None
    if t["kind"] == "pier":
        reason = ("modal pier forces are combined from internal cut "
                  "vectors that are not kept per mode")
    elif getattr(rs, "direction", "X") not in ("X", "Y"):
        reason = "the per-mode breakdown covers RS cases in X or Y"
    if reason is None:
        eng = _engine_cls()(model)
        if _mcx.wants_rs(rs):
            reason = "the case uses a named modal case"
        elif _mdx is not None and _mdx.wants(eng, rs):
            reason = "the case uses per-mode modal damping overrides"
        elif _rsx._crx.is_extended(rs):
            reason = ("the case uses eccentricity / angle / multiple "
                      "directions (extended RS)")
    if reason:
        out.update(supported=False,
                   message=f"No per-mode breakdown: {reason}. "
                           f"The combined value is {out['value']}.")
        return out
    ck = (cache_key, "rs", name)
    if cache_key is not None and ck in _CACHE:
        _CACHE.move_to_end(ck)
        modal_rows, per_mode, omegas = _CACHE[ck]
    else:
        spectrum = eng._resolve_rs_spectrum(rs)
        n_req = rs.num_modes if rs.num_modes > 0 else None
        modal = eng.run_modal(n_req)
        if not modal.periods:
            out.update(supported=False,
                       message="The model has no dynamic modes.")
            return out
        asm = eng._asm
        mass_map = dict(asm.mass_map)
        gkey = "gamma_x" if rs.direction == "X" else "gamma_y"
        per_mode, omegas, modal_rows = [], [], []
        for i, T in enumerate(modal.periods, start=1):
            omega = 2.0 * math.pi / T
            gamma = modal.participation[i - 1][gkey]
            sa = _oe._interp_spectrum(spectrum, T) * rs.scale * _oe.G_ACCEL
            phi = modal.shapes[i]
            loads = {(tg, dof): gamma * sa * m * phi[tg][dof - 1]
                     for (tg, dof), m in mass_map.items()}
            per_mode.append({"case": eng._modal_static(loads).to_dict(),
                             "piers": {}})
            omegas.append(omega)
            modal_rows.append({"mode": i, "period": T, "gamma": gamma,
                               "sa": sa})
        if cache_key is not None:
            _CACHE[ck] = (modal_rows, per_mode, omegas)
            while len(_CACHE) > MAX_CACHE:
                _CACHE.popitem(last=False)
    vals = np.array([extract(p, t) or 0.0 for p in per_mode])
    method = getattr(rs, "combo_method", "CQC")
    legacy = _mc.is_legacy(rs)
    if method == "SRSS":
        rho = np.eye(len(omegas))
    else:
        rho = _oe._cqc_matrix(omegas, rs.damping)
    if method == "ABS":
        R = float(np.sum(np.abs(vals)))
        contrib = np.abs(vals)
        cross = 0.0
    else:
        R2 = float(vals @ rho @ vals)
        R = math.sqrt(max(R2, 0.0))
        contrib = vals * (rho @ vals)             # sum = R^2
        cross = R2 - float(vals @ vals)
    rows = []
    for mr, v, c in zip(modal_rows, vals, contrib):
        rows.append({"kind": "mode", "name": f"Mode {mr['mode']}",
                     "mode": mr["mode"], "period": mr["period"],
                     "gamma": mr["gamma"], "sa": mr["sa"],
                     "unit_value": float(v), "modal_value": float(v),
                     "contribution": float(c),
                     "link": {"type": "mode", "mode": mr["mode"]}})
    den = (R if method == "ABS" else R * R)
    for r in rows:
        r["share"] = (r["contribution"] / den) if den else 0.0
        r["abs_share"] = abs(r["share"])
        r["srss_share"] = (r["modal_value"] ** 2 / float(vals @ vals)
                           if float(vals @ vals) else 0.0)
    out["rows"] = _rank(rows)
    out["computed_total"] = R
    out["cross_terms"] = cross
    out["sum_squares"] = float(vals @ vals)
    out["check"] = _check(out["value"], R)
    notes = [f"{len(rows)} modes combined by {method}"
             + ("" if legacy or method in ("SRSS", "CQC", "ABS") else
                " (shares shown with the CQC correlation; the case total "
                "uses the " + method + " rule)") + ".",
             "Modal values are signed; the combined result is positive "
             "(response-spectrum results carry no sign)."]
    if method not in ("SRSS", "ABS"):
        notes.append(f"Cross terms Σ(i≠j) ρij·ri·rj = {cross:.6g} "
                     f"(R² = Σri² + cross = {float(vals @ vals):.6g} + "
                     f"{cross:.6g}).")
    if not legacy and method not in ("SRSS", "CQC", "ABS"):
        out["check"]["ok"] = None
    out["notes"] = notes
    return out


def explain_th(model, results: dict, t: dict, name: str,
               top: int = 10) -> dict:
    th = (results.get("th_cases") or {}).get(name)
    out = {"case_kind": "time_history", "method": "governing-step"}
    if not isinstance(th, dict):
        out.update(supported=False,
                   message=f"No results for time-history case {name!r}.")
        return out
    ser = th_series(th, t, model)
    if not ser:
        pk = None
        if t["kind"] == "story":
            pk = th.get("peaks", {}).get("story", {}).get(
                t["story"], {}).get(t["component"])
        elif t["kind"] == "base":
            pk = th.get("peaks", {}).get("base", {}).get(t["component"])
        out.update(supported=False, value=pk,
                   message=("The time history of this quantity is not "
                            "recorded (TH output keeps story ux / uy and "
                            "base FX / FY series)"
                            + (f"; its peak is {pk:.6g}." if pk is not None
                               else ".")))
        return out
    tt = th.get("t") or [(k + 1) * 1.0 for k in range(len(ser))]
    order = sorted(range(len(ser)), key=lambda k: -abs(ser[k]))[:top]
    rows = [{"kind": "step", "name": f"Step {k + 1}", "step": k + 1,
             "time": float(tt[k]) if k < len(tt) else None,
             "unit_value": float(ser[k]), "contribution": float(ser[k]),
             "link": {"type": "step", "case": name, "step": k + 1}}
            for k in order]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["governs"] = i == 1
    g = rows[0] if rows else None
    out["rows"] = rows
    out["value"] = g["unit_value"] if g else 0.0
    out["max"] = max(ser)
    out["min"] = min(ser)
    out["governing_step"] = g["step"] if g else None
    out["governing_time"] = g["time"] if g else None
    out["notes"] = [f"Governing step {g['step']} at t = {g['time']:.4g} s "
                    f"of {len(ser)} (largest |value|)." if g else
                    "Empty history.",
                    f"Max {out['max']:.6g}, min {out['min']:.6g}."]
    return out


def explain(model, results: dict, target: dict, case: str,
            expand: bool = True, cache_key=None) -> dict:
    """Contribution breakdown of ``target`` in ``case`` (see module doc).

    Never mutates ``model`` or ``results``."""
    t0 = time.perf_counter()
    t = normalize_target(target)
    if not isinstance(results, dict):
        raise ValueError("no results: run the analysis first")
    if not isinstance(case, str) or not case:
        raise ValueError("'case' must name a load case or combination")
    base = {"target": dict(t, label=target_label(t), unit=unit_kind(t)),
            "case": case, "supported": True}
    if case in model.combos:
        r = explain_combo(model, results, t, case, expand, cache_key)
    elif case in model.cases:
        r = explain_static(model, results, t, case, cache_key)
    elif case in model.rs_cases:
        r = explain_rs(model, results, t, case, cache_key)
    elif case in model.th_cases:
        r = explain_th(model, results, t, case)
    else:
        kinds = model.case_kinds()
        kind = kinds.get(case)
        if kind is None and case in (getattr(model, "rs_combos", None) or {}):
            kind = "rs_directional"
        if kind is None:
            raise ValueError(f"unknown case or combination {case!r}")
        r = {"case_kind": kind, "supported": False, "method": "none",
             "message": _UNSUPPORTED.get(kind, (
                 f"{kind.replace('_', ' ')} results are not a "
                 "superposition of load patterns, so no contribution "
                 "breakdown is available."))}
    base.update(r)
    if base.get("supported") is not False:
        base["supported"] = True
    if t["kind"] == "member_force":
        cd = ((results.get("cases") or {}).get(case)
              or (results.get("combos") or {}).get(case)
              or (results.get("rs_cases") or {}).get(case) or {})
        x = station_x(cd, t)
        if x is not None:
            base["target"]["x"] = x
    base.setdefault("rows", [])
    base.setdefault("notes", [])
    base["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return _clean(base)


_UNSUPPORTED = {
    "pushover": ("Pushover results follow a nonlinear load path (hinges "
                 "yield step by step), so they cannot be split into "
                 "pattern contributions. Use the pushover curve and hinge "
                 "results instead."),
    "staged": ("Staged-construction results accumulate stage by stage "
               "with changing structure; they are not a superposition of "
               "patterns."),
    "nonlinear_static": ("Nonlinear static results are path dependent "
                         "and cannot be split into pattern contributions."),
    "buckling": "Buckling results are eigenvalues, not load effects.",
    "modal": "Modal results are mode shapes, not load effects.",
    "rs_directional": ("Directional RS combinations combine whole RS "
                       "cases; explain each RS case for its modal "
                       "breakdown."),
}


def _clean(o):
    """JSON-safe copy (NaN / inf -> None, numpy -> float)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        o = float(o)
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def clear_cache() -> None:
    _CACHE.clear()
