"""Side-by-side compare view: field differences of two results states
(Wave H, beyond ETABS; CONTRACT "Windows, pop-outs and side-by-side
compare (Wave H)").

The compare view shows two cases / steps of one run, or the same case of
two runs (an earlier analysis of this session vs the current one), in two
linked windows, plus an optional third DIFFERENCE window: contours of the
joint-displacement difference and diagrams of the frame-force difference
``B - A``.  This module computes those fields; the browser draws them
through the Wave G results layer (``js/h_compare_core.js`` is the exact JS
mirror used for step / envelope states, pinned to the same numbers by
``tests/test_h_windows.py``).

Rules (all values SI, as stored in the results):

* a *state* is the non-multi case block of a results dict (Wave G keys:
  ``c:<case>``, ``k:<combo>``, ``rs:<rs case>``; ``env`` ``"max"`` |
  ``"min"`` picks the envelope side of a max / min block) or a mode shape
  ``modal:<n>``;
* joints of B are matched to joints of A by TAG when the tag exists in both
  runs at the same coordinates (within ``tol``), otherwise by COORDINATES
  (rounded to ``tol``); unmatched joints are listed in ``only_a`` /
  ``only_b`` and get no difference;
* frame stations are matched by member uid; when the station abscissae
  differ, B is linearly interpolated onto A's stations (the ETABS station
  rule of :func:`g_core.valueAt`);
* a value pair is *flagged* by the Compare Results rule of
  :mod:`skyframe.core.results_compare` / :mod:`skyframe.core.results_diff`:
  ``|d| > max(atol, rtol * S)`` with ``S`` the family scale of the
  quantity in A (``results_compare._scales``);
* ``stats`` holds, per quantity, the largest absolute difference, where it
  occurs, the scale ``S`` and ``max_abs / S`` (``rel``); for the joint
  displacement the norm of the translation difference is reported too
  (``max_trans`` / ``at``).

Read-only: the inputs are never modified.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from skyframe.core import results_compare as _rc

QUANTITIES = ("node_disp", "reactions", "member_stations")
STATION_COMPS = ("N", "V2", "V3", "T", "M2", "M3")


def _coll(p: str) -> str:
    """An identifier-like key collapses in results_compare._family."""
    return "*" if any(c.isdigit() for c in p) or any(c in p for c in " -+") else p


def scales(ref) -> Dict[str, float]:
    """``results_compare._scales`` of a state (identical keys / values,
    pinned by tests) with the key collapse memoised per key string — the
    engine walks every path through ``_family``, 4x slower on a station
    block."""
    sc: Dict[str, float] = {}
    coll: Dict[str, str] = {}
    grp: Dict[str, str] = {}

    def walk(obj, fam):
        if isinstance(obj, dict):
            for k, v in obj.items():
                k = str(k)
                c = coll.get(k)
                if c is None:
                    c = coll[k] = _coll(k)
                walk(v, fam + (c,))
        elif isinstance(obj, (list, tuple)):
            f2 = fam + ("#",)
            for v in obj:
                walk(v, f2)
        elif isinstance(obj, (int, float)) and not isinstance(obj, bool) \
                and math.isfinite(obj):
            f = ".".join(fam)
            a = abs(float(obj))
            if a > sc.get(f, -1.0):
                sc[f] = a
            g = grp.get(f)
            if g is None:
                g = grp[f] = "~" + _rc._group(f)
            if a > sc.get(g, -1.0):
                sc[g] = a
    for q, v in (ref or {}).items():
        if q in _rc.NAME_KEYED:                 # (not used for states; keep the engine rule)
            return _rc._scales(ref)
        walk(v, (str(q),))
    return sc


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def case_state(results: dict, key: str, env: str = "max") -> dict:
    """The displayed state of a non-multi Wave G case key."""
    if not isinstance(results, dict):
        raise ValueError("results must be a JSON object")
    key = str(key or "")
    if key.startswith("modal:"):
        try:
            n = int(key.split(":", 1)[1])
        except ValueError:
            raise ValueError(f"bad mode key {key!r}") from None
        shapes = (results.get("modal") or {}).get("shapes") or {}
        shp = shapes.get(str(n))
        if shp is None:
            raise ValueError(f"no mode {n} in the results")
        return {"node_disp": shp}
    blocks = {"c": "cases", "k": "combos", "rs": "rs_cases"}
    pre, _, name = key.partition(":")
    if pre not in blocks or not name:
        raise ValueError(f"unsupported case key {key!r} (use c:, k:, rs: "
                         "or modal:<n>; steps are compared in the browser)")
    blk = (results.get(blocks[pre]) or {}).get(name)
    if not isinstance(blk, dict):
        raise ValueError(f"no {blocks[pre]} entry {name!r} in the results")
    if env == "min":
        if not isinstance(blk.get("min"), dict):
            raise ValueError(f"{name!r} has no envelope minimum")
        blk = blk["min"]
    return {q: blk.get(q) for q in QUANTITIES if blk.get(q) is not None}


def _key(p, tol):
    return tuple(int(round(float(c) / tol)) for c in p[:3])


def node_map(a: dict, b: dict, tol: float = 1e-6) -> Dict[str, str]:
    """B joint tag -> A joint tag (same tag at the same place, else the
    joint of A at B's coordinates)."""
    na = (a or {}).get("nodes") or {}
    nb = (b or {}).get("nodes") or {}
    if a is b or not na or not nb:
        return {str(t): str(t) for t in (nb or na)}
    by_xyz = {}
    for t, p in na.items():
        by_xyz.setdefault(_key(p, tol), str(t))
    out = {}
    for t, p in nb.items():
        t = str(t)
        pa = na.get(t)
        if pa is not None and all(abs(float(x) - float(y)) <= tol
                                  for x, y in zip(pa[:3], p[:3])):
            out[t] = t
            continue
        hit = by_xyz.get(_key(p, tol))
        if hit is not None:
            out[t] = hit
    return out


def _interp(xs: List[float], vals: List[float], x: float) -> float:
    """Linear interpolation, clamped at the ends (g_core.valueAt)."""
    n = len(xs)
    if not n:
        return 0.0
    if x <= xs[0]:
        return float(vals[0])
    if x >= xs[-1]:
        return float(vals[-1])
    for i in range(1, n):
        if x <= xs[i]:
            x0, x1 = xs[i - 1], xs[i]
            if x1 - x0 <= 1e-15:
                return float(vals[i])
            f = (x - x0) / (x1 - x0)
            return float(vals[i - 1]) + f * (float(vals[i]) - float(vals[i - 1]))
    return float(vals[-1])


def _vec_diff(va, vb) -> Optional[List[float]]:
    if not isinstance(va, (list, tuple)) or not isinstance(vb, (list, tuple)):
        return None
    n = min(len(va), len(vb))
    return [float(vb[i]) - float(va[i]) if _finite(va[i]) and _finite(vb[i])
            else 0.0 for i in range(n)]


def _map_diff(ma: dict, mb: dict, nmap: Dict[str, str]) -> Tuple[dict, List[str], List[str]]:
    ma = ma or {}
    mb = mb or {}
    inv: Dict[str, str] = {}
    for tb, ta in nmap.items():
        inv.setdefault(ta, tb)
    out, only_a = {}, []
    for ta, va in ma.items():
        tb = inv.get(str(ta))
        if tb is None or tb not in mb:
            only_a.append(str(ta))
            continue
        d = _vec_diff(va, mb[tb])
        if d is not None:
            out[str(ta)] = d
    mapped = set(inv.values())
    only_b = sorted(str(t) for t in mb if str(t) not in mapped)
    return out, sorted(only_a), only_b


def _stations_diff(sa: dict, sb: dict) -> Tuple[dict, List[str], List[str]]:
    sa = sa or {}
    sb = sb or {}
    out = {}
    for uid, a in sa.items():
        b = sb.get(uid)
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        xa, xb = a.get("x") or [], b.get("x") or []
        same = len(xa) == len(xb) and all(
            abs(float(p) - float(q)) <= 1e-9 for p, q in zip(xa, xb))
        d = {"x": [float(x) for x in xa]}
        for c in STATION_COMPS:
            ca, cb = a.get(c), b.get(c)
            if not isinstance(ca, list) or not isinstance(cb, list):
                continue
            if same:
                d[c] = [float(cb[i]) - float(ca[i]) for i in range(len(ca))]
            elif xb and len(cb) == len(xb):
                d[c] = [_interp(xb, cb, float(xa[i])) - float(ca[i])
                        for i in range(len(ca))]
        out[str(uid)] = d
    only_a = sorted(str(u) for u in sa if u not in sb)
    only_b = sorted(str(u) for u in sb if u not in sa)
    return out, only_a, only_b


def field_difference(state_a: dict, state_b: dict,
                     nmap: Optional[Dict[str, str]] = None, *,
                     rtol: float = 1e-6, atol: float = 0.0) -> dict:
    """``B - A`` of two states (keys of A's joints / members)."""
    if not (math.isfinite(rtol) and rtol >= 0.0) or \
            not (math.isfinite(atol) and atol >= 0.0):
        raise ValueError("rtol / atol must be finite values >= 0")
    state_a = state_a or {}
    state_b = state_b or {}
    if nmap is None:
        tags = set(map(str, (state_a.get("node_disp") or {}))) | set(
            map(str, (state_b.get("node_disp") or {})))
        nmap = {t: t for t in tags}
    out: Dict[str, Any] = {"only_a": {}, "only_b": {}, "stats": {}}
    for q in ("node_disp", "reactions"):
        if q in state_a or q in state_b:
            d, oa, ob = _map_diff(state_a.get(q), state_b.get(q), nmap)
            out[q] = d
            out["only_a"][q] = oa
            out["only_b"][q] = ob
    if "member_stations" in state_a or "member_stations" in state_b:
        d, oa, ob = _stations_diff(state_a.get("member_stations"),
                                   state_b.get("member_stations"))
        out["member_stations"] = d
        out["only_a"]["member_stations"] = oa
        out["only_b"]["member_stations"] = ob
    # family scales of run A (Compare Results engine)
    sc = scales({q: state_a.get(q) for q in QUANTITIES
                 if state_a.get(q) is not None})
    memo: Dict[tuple, float] = {}

    def scale(path) -> float:
        # the family of a path depends only on the collapsed keys: cache by them
        k = tuple(p if isinstance(p, int) else _coll(p) for p in path[1:])
        k = (path[0],) + tuple(0 if isinstance(p, int) else p for p in k)
        v = memo.get(k)
        if v is None:
            v = memo[k] = _rc._scale(sc, path)
        return v
    n_flag = 0
    for q in ("node_disp", "reactions"):
        if q not in out:
            continue
        best, at, trans, tat = 0.0, None, 0.0, None
        S = 0.0
        for t, d in out[q].items():
            for i, v in enumerate(d):
                s = scale((q, t, i))
                S = max(S, s)
                if abs(v) > max(atol, rtol * s):
                    n_flag += 1
                if abs(v) > best:
                    best, at = abs(v), [t, i]
            n3 = math.sqrt(sum(v * v for v in d[:3]))
            if n3 > trans:
                trans, tat = n3, t
        st = {"max_abs": best, "at": at, "scale": S,
              "rel": (best / S) if S > 0 else (0.0 if best == 0 else math.inf)}
        if q == "node_disp":
            st["max_trans"] = trans
            st["at_trans"] = tat
        out["stats"][q] = st
    if "member_stations" in out:
        per: Dict[str, dict] = {}
        for uid, d in out["member_stations"].items():
            for c, vals in d.items():
                if c == "x":
                    continue
                s = scale(("member_stations", uid, c, 0))
                e = per.setdefault(c, {"max_abs": 0.0, "at": None,
                                       "scale": 0.0})
                e["scale"] = max(e["scale"], s)
                for k, v in enumerate(vals):
                    if abs(v) > max(atol, rtol * s):
                        n_flag += 1
                    if abs(v) > e["max_abs"]:
                        e["max_abs"], e["at"] = abs(v), [uid, d["x"][k]]
        for c, e in per.items():
            e["rel"] = (e["max_abs"] / e["scale"]) if e["scale"] > 0 else \
                (0.0 if e["max_abs"] == 0 else math.inf)
        out["stats"]["member_stations"] = per
    out["n_flagged"] = n_flag
    out["rtol"] = rtol
    out["atol"] = atol
    out["equal"] = n_flag == 0
    return out


def compare_fields(a: dict, b: dict, case_a: str, case_b: Optional[str] = None,
                   *, env: str = "max", rtol: float = 1e-6,
                   atol: float = 0.0, tol: float = 1e-6) -> dict:
    """Difference fields of case ``case_b`` of run ``b`` against case
    ``case_a`` of run ``a`` (``case_b`` defaults to ``case_a``)."""
    if env not in ("max", "min"):
        raise ValueError("env must be 'max' or 'min'")
    case_b = case_b or case_a
    sa = case_state(a, case_a, env)
    sb = case_state(b, case_b, env)
    nmap = node_map(a, b, tol)
    out = field_difference(sa, sb, nmap, rtol=rtol, atol=atol)
    out["case_a"] = case_a
    out["case_b"] = case_b
    out["env"] = env
    out["same_run"] = a is b
    out["n_nodes_matched"] = len(nmap)
    return _json_safe(out)


def _json_safe(o):
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json_safe(v) for v in o]
    return o
