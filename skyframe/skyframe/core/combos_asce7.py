"""ASCE 7 combinations with grouped pattern types (Wave B).

CONTRACT "Irregularity checks, grouped ASCE 7 combinations, E2K releases /
joint axes, joint-local reactions, RS shell forces in combos, TS 498 / KDS /
CFE wind, NTC long-period distribution".

:func:`skyframe.core.codes.asce7_combinations` keeps its legacy behaviour
for every model whose single-pattern cases use only the legacy kinds
(``dead`` / ``live`` / ``quake`` / ``wind``): the first dead case is D, the
first live case is L.  As soon as ONE case uses a grouped type
(:data:`GROUPED_KINDS`: ``superdead``, ``reducible_live``, ``roof_live``,
``snow``) the generator switches to this module, which classifies the cases
exactly like :func:`skyframe.core.combos_codes.classify` (a case belongs to
a group when every pattern it applies maps to that group):

=================  =====  =================================================
pattern kind       group  ASCE 7 symbol
=================  =====  =================================================
dead, superdead    G      D  (all cases, same factor)
live, red. live    L      L  (all cases, same factor)
roof_live          Lr     Lr (all cases, same factor)
snow               S      S  (all cases, same factor)
wind               W      W  (one case at a time, +/- sign variants)
quake              E      E  (one case at a time, +/- sign variants)
=================  =====  =================================================

Roof live stays its own group (ASCE 7 §2.3.1 combinations 2-4 treat
``Lr or S or R`` as one alternative), the way ``combos_codes`` does for the
other codes; ``(Lr or S)`` expands to one combination per present group.

Strength (LRFD, ASCE 7-16 §2.3.1 / 7-22 §2.3.1)::

    1  1.4D
    2  1.2D + 1.6L + 0.5(Lr or S)
    3  1.2D + 1.6(Lr or S) + (L or 0.5W)
    4  1.2D + 1.0W + L + 0.5(Lr or S)
    5  0.9D + 1.0W
    6  1.2D + 1.0E + L + 0.2S            (Ev = 0.2 SDS D folded into D)
    7  0.9D + 1.0E                       (Ev subtracted)

Allowable stress (ASD, §2.4.1)::

    1  D
    2  D + L
    3  D + (Lr or S)
    4  D + 0.75L + 0.75(Lr or S)
    5  D + 0.6W            D + 0.7E
    6  D + 0.75L + 0.75(0.6W) + 0.75(Lr or S)
       D + 0.75L + 0.75(0.7E) + 0.75S
    7  0.6D + 0.6W         0.6D + 0.7E

``SDS`` folds the vertical seismic component into the dead factor exactly
as the legacy generator (LRFD 1.2+0.2SDS / 0.9-0.2SDS; ASD 1.0+0.14SDS /
1.0+0.105SDS / 0.6-0.14SDS).  RS cases are not used (the legacy generator
does not use them either).
"""
from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Tuple

#: pattern kinds that switch asce7_combinations to the grouped generator
GROUPED_KINDS = ("superdead", "reducible_live", "roof_live", "snow")

_SIGNS = ((1.0, "+"), (-1.0, "-"))


def grouped_applies(model) -> bool:
    """True when a case applies a pattern of one of :data:`GROUPED_KINDS`
    (models with only the legacy kinds keep the legacy generator)."""
    for case in model.cases.values():
        for pname in case.patterns:
            pat = model.patterns.get(pname)
            if pat is not None and pat.kind in GROUPED_KINDS:
                return True
    return False


def _classify(model):
    from .combos_codes import classify
    cls = classify(model, include_rs=False)
    for msg in cls.get("warnings", []):
        warnings.warn("ASCE 7 combinations: " + msg, UserWarning)
    return cls


def asce7_grouped(model, standard: str, sds: float
                  ) -> Dict[str, Dict[str, float]]:
    """Grouped ASCE 7 combinations (see the module docstring)."""
    cls = _classify(model)
    G, L, Lr, S = cls["G"], cls["L"], cls["Lr"], cls["S"]
    quakes = [c for c, _s in cls["E"]]
    winds = [c for c, _s in cls["W"]]
    combos: Dict[str, Dict[str, float]] = {}
    if not G:
        warnings.warn("ASCE 7 combinations: no DEAD case found; no "
                      "combinations generated", UserWarning)
        return combos
    if not L:
        warnings.warn("ASCE 7 combinations: no LIVE case found; live terms "
                      "omitted", UserWarning)
    roofs: List[Tuple[str, List[str]]] = [(g, cls[g]) for g in ("Lr", "S")
                                          if cls[g]]

    def put(name: str, terms: List[Tuple[List[str], float]],
            lat: Optional[Tuple[str, float]] = None) -> None:
        fac: Dict[str, float] = {}
        for cases, f in terms:
            if f == 0.0:
                continue
            for c in cases:
                fac[c] = fac.get(c, 0.0) + f
        if lat is not None:
            fac[lat[0]] = fac.get(lat[0], 0.0) + lat[1]
        combos[name] = fac

    lterm = [(L, 1.0)] if L else []
    if standard == "LRFD":
        put("1.4D", [(G, 1.4)])
        # 2: 1.2D + 1.6L + 0.5(Lr or S)
        base2 = "1.2D" + ("+1.6L" if L else "")
        if roofs:
            for g, cs in roofs:
                put(f"{base2}+0.5{g}", [(G, 1.2), (L, 1.6), (cs, 0.5)])
        else:
            put(base2, [(G, 1.2), (L, 1.6)])
        # 3: 1.2D + 1.6(Lr or S) + (L or 0.5W)
        for g, cs in roofs:
            if L:
                put(f"1.2D+1.6{g}+1.0L", [(G, 1.2), (cs, 1.6), (L, 1.0)])
            else:
                put(f"1.2D+1.6{g}", [(G, 1.2), (cs, 1.6)])
            for w in winds:
                for sgn, sl in _SIGNS:
                    put(f"1.2D+1.6{g}{sl}0.5{w}", [(G, 1.2), (cs, 1.6)],
                        (w, sgn * 0.5))
        # 4 / 5: wind
        for w in winds:
            for sgn, sl in _SIGNS:
                tail = "+1.0L" if L else ""
                if roofs:
                    for g, cs in roofs:
                        put(f"1.2D{tail}+0.5{g}{sl}1.0{w}",
                            [(G, 1.2)] + lterm + [(cs, 0.5)], (w, sgn))
                else:
                    put(f"1.2D{tail}{sl}1.0{w}", [(G, 1.2)] + lterm,
                        (w, sgn))
                put(f"0.9D{sl}1.0{w}", [(G, 0.9)], (w, sgn))
        # 6 / 7: seismic (+0.2S)
        d_add, d_min = (1.2 + 0.2 * sds, 0.9 - 0.2 * sds) if sds else \
            (1.2, 0.9)
        for q in quakes:
            for sgn, sl in _SIGNS:
                tail = "+1.0L" if L else ""
                tail += "+0.2S" if S else ""
                put(f"{d_add:g}D{tail}{sl}1.0{q}",
                    [(G, d_add)] + lterm + [(S, 0.2)], (q, sgn))
                put(f"{d_min:g}D{sl}1.0{q}", [(G, d_min)], (q, sgn))
        return combos

    # ---------------------------------------------------------------- ASD
    put("D", [(G, 1.0)])
    if L:
        put("D+L", [(G, 1.0), (L, 1.0)])
    for g, cs in roofs:
        put(f"D+{g}", [(G, 1.0), (cs, 1.0)])
        if L:
            put(f"D+0.75L+0.75{g}", [(G, 1.0), (L, 0.75), (cs, 0.75)])
    for w in winds:
        for sgn, sl in _SIGNS:
            put(f"D{sl}0.6{w}", [(G, 1.0)], (w, sgn * 0.6))
            l75 = [(L, 0.75)] if L else []
            tl = "+0.75L" if L else ""
            if roofs:
                for g, cs in roofs:
                    put(f"D{tl}+0.75{g}{sl}0.45{w}",
                        [(G, 1.0)] + l75 + [(cs, 0.75)], (w, sgn * 0.45))
            else:
                put(f"D{tl}{sl}0.45{w}", [(G, 1.0)] + l75, (w, sgn * 0.45))
            put(f"0.6D{sl}0.6{w}", [(G, 0.6)], (w, sgn * 0.6))
    for q in quakes:
        if sds:
            da, da34, dm = (1.0 + 0.14 * sds, 1.0 + 0.105 * sds,
                            0.6 - 0.14 * sds)
        else:
            da, da34, dm = 1.0, 1.0, 0.6
        pre = "" if da == 1.0 else f"{da:g}"
        pre34 = "" if da34 == 1.0 else f"{da34:g}"
        for sgn, sl in _SIGNS:
            put(f"{pre}D{sl}0.7{q}", [(G, da)], (q, sgn * 0.7))
            l75 = [(L, 0.75)] if L else []
            tl = ("+0.75L" if L else "") + ("+0.75S" if S else "")
            put(f"{pre34}D{tl}{sl}0.525{q}",
                [(G, da34)] + l75 + [(S, 0.75)], (q, sgn * 0.525))
            put(f"{dm:g}D{sl}0.7{q}", [(G, dm)], (q, sgn * 0.7))
    return combos


__all__ = ["GROUPED_KINDS", "grouped_applies", "asce7_grouped"]
