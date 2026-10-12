"""Code-table auto hinges + gravity axial loads (Wave B) — engine side.

* :func:`prepare` runs at the START of ``_build`` (before the domain is
  wiped, like ``engine.pdelta.prepare``) for HINGED builds only: when any
  member carries an ``auto`` hinge whose generator uses the axial load, or
  a ``PMM`` interacting hinge with ``axial.source == "gravity"``, it solves
  the gravity load combination LINEARLY on a separate engine (a deep copy
  of the model with one extra static case) and caches the member station
  axial forces.  Gravity = the property's own ``gravity`` mapping, else the
  hinged case's ``gravity`` (pushover cases), else none (P_G = 0, noted).
* :func:`resolve` turns one assigned ``auto`` property into concrete B10
  lumped-hinge plan entries for that member (``design.hinge_tables``
  spec -> canonical property), so the zeroLength / material / recording
  path is the user-hinge one.  An end-j axial (brace) hinge swaps its
  positive / negative sides so tension stays positive at both ends.

Every model without auto / PMM hinges never reaches this module's solve:
``prepare`` returns immediately (bit-identical builds).
"""

from __future__ import annotations

import copy
import warnings
from typing import Dict, List, Optional, Tuple

from skyframe.design import hinge_tables as _ht

_CASE = "__AUTO_HINGE_GRAVITY__"


def _gkey(grav: Optional[dict]) -> Tuple:
    return tuple(sorted((grav or {}).items()))


def _assigned(model):
    props = getattr(model, "hinge_properties", {}) or {}
    for m in model.members:
        hl = getattr(m, "hinges", None)
        if not isinstance(hl, list):
            continue
        for h in hl:
            p = props.get(h.get("property"))
            if p is not None:
                yield m, h, p


def _gravity_of(p: dict, case_grav: Optional[dict]) -> Optional[dict]:
    """The gravity mapping an axial-dependent property uses (None = it
    does not need one)."""
    if p["type"] == "auto":
        if not _ht.needs_axial(p["auto"]) or p["auto"]["P"] is not None:
            return None
        return p["auto"]["gravity"] if p["auto"]["gravity"] is not None \
            else (case_grav or {})
    if p["type"] == "PMM" and p["axial"]["source"] in ("gravity",
                                                      "analysis"):
        return p["axial"]["gravity"] if p["axial"]["gravity"] is not None \
            else (case_grav or {})
    return None


def prepare(eng, hinge_case) -> None:
    """Gravity axial forces for axial-dependent hinges (start of _build)."""
    if hinge_case is None:
        return
    model = eng.model
    case_grav = dict(getattr(hinge_case, "gravity", {}) or {})
    eng._ah_case_gravity = case_grav
    need = set()
    for _, _, p in _assigned(model):
        g = _gravity_of(p, case_grav)
        if g:
            need.add(_gkey(g))
    cache = getattr(eng, "_ah_axial", None)
    if cache is None:
        cache = eng._ah_axial = {}
    for key in need:
        if key in cache:
            continue
        cache[key] = _solve(model, dict(key))


def _solve(model, grav: dict) -> Dict[str, dict]:
    """uid -> {"x": stations (m), "P": compression-positive axial} of a
    linear static solve of ``grav`` on a separate engine."""
    from skyframe.core.model import LoadCase
    from skyframe.engine import opensees_engine as _oe
    for pat in grav:
        if pat not in model.patterns:
            raise ValueError(f"auto hinges: gravity pattern {pat!r} is not "
                             "defined")
    sub = copy.deepcopy(model)
    sub.cases[_CASE] = LoadCase(_CASE, dict(grav))
    res = _oe.OpenSeesEngine(sub).run_static(_CASE)
    _oe._REUSE["owner"] = None                 # the outer build rebuilds
    _oe._REUSE["loaded"] = False
    out = {}
    for uid, st in res.member_stations.items():
        out[uid] = {"x": list(st["x"]), "P": [-float(n) for n in st["N"]]}
    return out


def axial_at(eng, m, d: float, grav: Optional[dict]) -> float:
    """Compression-positive gravity axial force of member ``m`` at
    relative distance ``d`` (0 when there is no gravity)."""
    if not grav:
        return 0.0
    st = (getattr(eng, "_ah_axial", {}) or {}).get(_gkey(grav), {}).get(
        m.uid)
    if not st:
        return 0.0
    xs, Ps = st["x"], st["P"]
    x = d * m.length
    if x <= xs[0]:
        return Ps[0]
    for (x0, p0), (x1, p1) in zip(zip(xs, Ps), zip(xs[1:], Ps[1:])):
        if x <= x1:
            return p0 + (p1 - p0) * (x - x0) / (x1 - x0) if x1 > x0 else p1
    return Ps[-1]


def pmm_axial(eng, m, d: float, prop: dict) -> Tuple[float, str]:
    """(P in the ETABS surface sign — compression NEGATIVE, source)."""
    ax = prop["axial"]
    if ax["source"] == "value":
        return ax["value"], "value"
    grav = _gravity_of(prop, getattr(eng, "_ah_case_gravity", {}))
    src = ax["source"]                       # gravity | analysis (wave C)
    if not grav:
        return 0.0, f"{src} (none: P = 0)"
    return -axial_at(eng, m, d, grav), src


def resolve(eng, m, name: str, p: dict, d: float) -> List[dict]:
    """B10 plan entries for one assigned auto hinge (may be empty)."""
    auto = p["auto"]
    if auto["P"] is not None:
        P_G, src = auto["P"], "value"
    else:
        grav = _gravity_of(p, getattr(eng, "_ah_case_gravity", {}))
        P_G = axial_at(eng, m, d, grav)
        src = "gravity" if grav else ("none" if _ht.needs_axial(auto)
                                      else "n/a")
    specs, why = _ht.generate(eng.model, m, auto, P_G)
    if not specs:
        warnings.warn(f"auto hinge {name!r} on member {m.uid!r}: {why}",
                      UserWarning)
        return []
    if auto.get("axial_update"):         # wave C: coupled, P-updated PMM
        from skyframe.design import hinge_tables_c as _htc
        grav = _gravity_of(p, getattr(eng, "_ah_case_gravity", {}))
        ip = _htc.auto_pmm_property(eng.model, m, auto, specs, p, grav)
        if ip is not None:
            s3 = next(s for s in specs if s["type"] == "M3")
            meta = dict(s3["meta"], P_G=P_G, axial_source="analysis",
                        thy=s3["thy"], dof="PMM", axial_update=True)
            return [{"property": name, "prop": ip, "d": d,
                     "t": d * m.length, "placed": False, "auto": meta}]
        warnings.warn(f"auto hinge {name!r} on member {m.uid!r}: no "
                      "interaction surface (axial_update ignored)",
                      UserWarning)
    out = []
    for spec in specs:
        prop = _ht.spec_to_property(spec, p)
        acc_neg = spec.get("acc_neg")
        if spec["type"] == "P" and d >= 1.0 and spec.get("neg"):
            # end j: tension is the NEGATIVE zeroLength deformation
            bb = prop["backbone"]
            prop["backbone"] = {"positive": bb["negative"],
                                "negative": bb["positive"]}
            prop["acceptance"], acc_neg = dict(acc_neg), dict(spec["acc"])
        meta = dict(spec["meta"], P_G=P_G, axial_source=src,
                    thy=spec["thy"], dof=auto["dof"])
        out.append({"property": name, "prop": prop, "d": d,
                    "t": d * m.length, "placed": False, "auto": meta,
                    "acc_neg": acc_neg})
    return out


def preview(model, m, p: dict, P_G: float = 0.0, d: float = 0.0
            ) -> dict:
    """UI preview (POST /api/hinges/auto-preview): the generated specs of
    one member at an explicit P_G, no analysis."""
    specs, why = _ht.generate(model, m, p["auto"], P_G)
    return {"uid": m.uid, "P_G": P_G, "reason": why,
            "hinges": [{"type": s["type"],
                        "property": _ht.spec_to_property(s, p),
                        "acceptance_negative": s.get("acc_neg"),
                        "thy": s["thy"], "meta": s["meta"]}
                       for s in specs]}
