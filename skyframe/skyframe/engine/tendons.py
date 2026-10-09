"""Engine side of the post-tensioning tendons (equivalent loads) and the
hyperstatic case — see :mod:`skyframe.core.tendons` for the mechanics and
CONTRACT "Post-tensioning tendons (as loads) and hyperstatic case".

* :func:`apply_pattern_tendons` is called by ``OpenSeesEngine._apply_pattern``
  (one line) so EVERY analysis that applies a load pattern (static, P-Delta,
  nonlinear static, model-wide P-Delta, pushover distributions, multi-
  component TH, steady state ...) receives the tendon loads of that pattern.
* :func:`run_hyperstatic` evaluates one hyperstatic case from the cached
  static-case results (no extra solve).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import openseespy.opensees as ops

from skyframe.core import tendons as _td

_TOL = 1e-6


def _tendons(eng) -> List[dict]:
    """Normalised tendons (cached on the engine; the model is frozen)."""
    cache = eng.__dict__.get("_tdn_norm")
    if cache is None:
        cache = [_td.normalize_tendon(t)
                 for t in (getattr(eng.model, "tendons", None) or [])]
        eng.__dict__["_tdn_norm"] = cache
    return cache


def _equiv(eng, td: dict) -> dict:
    cache = eng.__dict__.setdefault("_tdn_eq", {})
    eq = cache.get(td["uid"])
    if eq is None:
        eq = _td.equivalent_loads(eng.model, td)
        cache[td["uid"]] = eq
    return eq


def _member_loads(eng, td: dict) -> list:
    cache = eng.__dict__.setdefault("_tdn_ml", {})
    ml = cache.get(td["uid"])
    if ml is None:
        ml = _td.frame_member_loads(eng.model, td, _equiv(eng, td))
        cache[td["uid"]] = ml
    return ml


def has_tendons(model) -> bool:
    return bool(getattr(model, "tendons", None))


def apply_pattern_tendons(eng, asm, pat_name: str, scale: float) -> None:
    """Add the (scaled) equivalent loads of every tendon of ``pat_name``
    into the active OpenSees pattern."""
    for td in _tendons(eng):
        if td["pattern"] != pat_name or scale == 0.0:
            continue
        if _td.host_kind(eng.model, td) == "frame":
            for uid, kind, w, a, b, dn in _member_loads(eng, td):
                member = eng._members_by_uid[uid]
                eng._apply_member_load(asm, member, kind, w * scale, 0.0,
                                       a, b, dn)
        else:
            _apply_shell(eng, asm, td, scale)


def _shell_nodes(eng, asm, td: dict) -> Dict[int, tuple]:
    hosts = set(td["host"])
    tags = sorted({n + 1 for q in asm.mesh.quads if q.region in hosts
                   for n in q.nodes})
    if not tags:
        raise ValueError(f"Tendon {td['uid']!r}: host shell region(s) "
                         f"{td['host']} have no shell mesh nodes")
    return {t: asm.node_coords[t] for t in tags}


def _apply_shell(eng, asm, td: dict, scale: float) -> None:
    """Shell hosts: each equivalent force goes to the NEAREST host-region
    mesh node with its transfer couple (exact equilibrium)."""
    nodes = _shell_nodes(eng, asm, td)
    eq = _equiv(eng, td)
    acc: Dict[int, List[float]] = {}
    for it in eq["points"] + eq["lines"]:
        p = it["point"]
        t = min(nodes, key=lambda k: (math.dist(nodes[k], p), k))
        F = _td._mul(it["force"], scale)
        M = _td._cross(_td._sub(p, nodes[t]), F)
        v = acc.setdefault(t, [0.0] * 6)
        for i in range(3):
            v[i] += F[i]
            v[3 + i] += M[i]
    for t in sorted(acc):
        ops.load(t, *acc[t])


# --------------------------------------------------------------------------- #
# hyperstatic case
# --------------------------------------------------------------------------- #
_KEYS = ("N", "V2", "V3", "T", "M2", "M3")


def run_hyperstatic(eng, name: str) -> dict:
    """Hyperstatic (secondary) effects of one hyperstatic case:
    secondary = total (the referenced static case) - primary (P*e)."""
    from skyframe.engine.opensees_engine import _local_axes
    model = eng.model
    hcs = getattr(model, "hyperstatic_cases", None) or {}
    if name not in hcs:
        raise ValueError(f"Unknown hyperstatic case {name!r}")
    ref = hcs[name]["case"]
    cr = eng.run_static(ref)
    pats = model.cases[ref].patterns
    tds = [(td, float(pats[td["pattern"]])) for td in _tendons(eng)
           if td["pattern"] in pats and pats[td["pattern"]] != 0.0]
    asm = eng._asm if eng._asm is not None else eng._build()
    members: Dict[str, dict] = {}
    for m in model.members:
        st = cr.member_stations.get(m.uid)
        if st is None:
            continue
        xs = list(st["x"])
        L = m.length
        xax, yax, zax, _v, _vert = _local_axes(m)
        seg_nodes = [s.x0 for s in asm.mesh.segments[m.uid][1:]]
        prim = {k: [0.0] * len(xs) for k in _KEYS}
        hosted = False
        for td, sc in tds:
            if m.uid not in td["host"]:
                continue
            eq = _equiv(eng, td)
            for j, x in enumerate(xs):
                left = (x >= L - 1e-9
                        or any(abs(x - x0) < 1e-9 for x0 in seg_nodes))
                if x <= 1e-9:
                    left = False
                v = _td.primary_at(model, td, eq, m, x, (xax, yax, zax),
                                   "left" if left else "right")
                if v is None:
                    continue
                hosted = True
                for k, val in zip(_KEYS, v):
                    prim[k][j] += sc * val
        tot = {k: list(st[k]) for k in _KEYS}
        sec = {k: [t - p for t, p in zip(tot[k], prim[k])] for k in _KEYS}
        members[m.uid] = {"x": xs, "hosts_tendon": hosted, "total": tot,
                          "primary": prim, "secondary": sec}
    return {"case": ref,
            "tendon_patterns": {td["pattern"]: sc for td, sc in tds},
            "reactions": {str(t): list(v) for t, v in cr.reactions.items()},
            "base": dict(cr.base),
            "members": members}


def report(eng) -> Dict[str, dict]:
    """results ``"tendons"``: per-tendon force profile + load summary."""
    return {td["uid"]: _td.tendon_report(eng.model, td, _equiv(eng, td))
            for td in _tendons(eng)}
