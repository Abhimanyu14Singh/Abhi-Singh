"""Significance gate for ratio-based story checks (CONTRACT "Ratio-based
story checks: negligible-drift significance").

Several story diagnostics divide one response quantity by another of the
same kind: the ASCE 7 §12.3.2.1 torsional ratio ``delta_max / delta_avg``
(Type 1a/1b), the Diaphragm Max Over Avg displacement / drift ratios, story
stiffness ``V / Delta`` and the Table 12.3-2 soft-story ratios built on it.
In a direction that a case does not load (e.g. Y under an X force on a
doubly symmetric plan) both numerator and denominator are round-off noise,
~1e-15 of the loaded response, so the "ratio" is a random number that can
flag "extreme torsional irregularity" (or an extreme soft story) at random.

The gate: a quantity ``q`` in the denominator of such a ratio is
**negligible** when

    |q| < threshold(ref) = max(ABS_FLOOR, REL_FLOOR * ref)

with ``ref`` = the largest story displacement of the case
(:func:`case_reference`; it bounds every interstory drift, ``Delta <= 2
ref``, and sets the scale of the round-off in every displacement and drift
of a linear solve), ``REL_FLOOR = 1e-6`` (ten orders of magnitude above
double-precision round-off, far below any engineering-relevant drift) and
``ABS_FLOOR = 1e-9`` m (one nanometre, for cases whose whole response is
noise).  A negligible ratio is reported as ``None`` with the reason
:data:`NA_NEGLIGIBLE_DRIFT` (see :func:`mark_na`); ratios whose denominator
passes the gate are computed by the unchanged legacy expressions, so every
genuinely loaded direction stays byte-identical.

Pure functions, no engine import.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, Mapping, Optional

ABS_FLOOR = 1e-9            # m
REL_FLOOR = 1e-6            # x the largest story displacement of the case
NA_NEGLIGIBLE_DRIFT = "not applicable (negligible drift)"
NA_KEY = "na_reason"


def threshold(ref: float) -> float:
    """``max(ABS_FLOOR, REL_FLOOR * |ref|)`` (m)."""
    return max(ABS_FLOOR, REL_FLOOR * abs(float(ref)))


def case_reference(story: Mapping[str, Mapping[str, float]]) -> float:
    """Largest ``|ux|`` / ``|uy|`` over all story entries of one case (m).

    ``story`` is a case's per-story block (``CaseResults.story`` or the
    ``"story"`` dict of its JSON) — ALL stories, all towers, so every
    per-tower / per-diaphragm view of a case shares one reference."""
    ref = 0.0
    for e in story.values():
        for k in ("ux", "uy"):
            v = e.get(k) if isinstance(e, Mapping) else None
            if isinstance(v, (int, float)):
                ref = max(ref, abs(float(v)))
    return ref


def case_threshold(story: Mapping[str, Mapping[str, float]]) -> float:
    return threshold(case_reference(story))


def negligible(q: float, thr: float) -> bool:
    """True when ``|q|`` is below the significance threshold."""
    return abs(float(q)) < thr


def tors_avg(u: float, rz: float, B: float) -> float:
    """``delta_avg`` of the two diaphragm ends at +/- B/2 (same expression
    as the engine's ``_tors_ratio`` denominator)."""
    d1 = abs(u + rz * B / 2.0)
    d2 = abs(u - rz * B / 2.0)
    return 0.5 * (d1 + d2)


def tors_ratio(u: float, rz: float, B: float, thr: float,
               legacy: Callable[[float, float, float], float]
               ) -> Optional[float]:
    """The legacy torsional ratio, or ``None`` when ``delta_avg`` (the
    average end displacement in that direction) is negligible."""
    if tors_avg(u, rz, B) < thr:
        return None
    return legacy(u, rz, B)


def tors_flag(ratios: Iterable[Optional[float]]) -> str:
    """ASCE 7 Table 12.3-1 flag over the APPLICABLE ratios ("none" when no
    ratio applies): >= 1.4 extreme (1b), >= 1.2 torsional (1a)."""
    vals = [r for r in ratios if r is not None]
    if not vals:
        return "none"
    trmax = max(vals)
    return ("extreme" if trmax >= 1.4 else
            "torsional" if trmax >= 1.2 else "none")


def mark_na(entry: Dict[str, object], key: str,
            reason: str = NA_NEGLIGIBLE_DRIFT) -> None:
    """Set ``entry[key] = None`` (when ``key`` is a ratio slot) and record
    ``entry["na_reason"][key] = reason``."""
    na = entry.get(NA_KEY)
    if not isinstance(na, dict):
        na = {}
        entry[NA_KEY] = na
    na[key] = reason


def clear_na(entry: Dict[str, object], key: str) -> None:
    """Drop ``key`` from ``entry["na_reason"]`` (and the dict when empty)."""
    na = entry.get(NA_KEY)
    if isinstance(na, dict):
        na.pop(key, None)
        if not na:
            entry.pop(NA_KEY, None)


def story_stiffness(v: float, d: float, thr: float) -> float:
    """``|V / Delta|`` (the legacy story-stiffness expression), 0.0 when
    either is ~0 — and now also when ``Delta`` is negligible."""
    if negligible(d, thr):
        return 0.0
    return abs(v / d) if abs(d) > 1e-15 and abs(v) > 1e-15 else 0.0


def soft_story(entry: Dict[str, object], idx: int, order: list,
               k_primary: Mapping[str, float], neg: set) -> None:
    """ASCE 7 Table 12.3-2 soft-story ratio into ``entry`` (keys
    ``stiff_ratio`` then ``soft_flag``), identical to the legacy rule unless a
    story involved has a negligible drift in the primary direction
    (``neg``): then ``stiff_ratio`` is ``None`` (n/a) when this story or the
    one above is negligible, and negligible stories drop out of the
    average of the three above."""
    name = order[idx]
    k_here = k_primary.get(name, 0.0)
    nxt = order[idx + 1] if idx + 1 < len(order) else None
    soft_flag = ""
    if name in neg or (nxt is not None and nxt in neg):
        entry["stiff_ratio"] = None
        entry["soft_flag"] = soft_flag
        mark_na(entry, "stiff_ratio")
        return
    k_above = k_primary.get(nxt) if nxt is not None else None
    above3 = [k_primary.get(n, 0.0) for n in order[idx + 1:idx + 4]
              if n not in neg]
    avg3 = (sum(above3) / len(above3)) if above3 else None
    if k_above and k_above > 1e-15:
        entry["stiff_ratio"] = k_here / k_above
        r = k_here / k_above
        ra = (k_here / avg3) if avg3 and avg3 > 1e-15 else None
        if r < 0.6 or (ra is not None and ra < 0.7):
            soft_flag = "extreme_soft"
        elif r < 0.7 or (ra is not None and ra < 0.8):
            soft_flag = "soft"
    else:
        entry["stiff_ratio"] = None
    entry["soft_flag"] = soft_flag


__all__ = ["ABS_FLOOR", "REL_FLOOR", "NA_NEGLIGIBLE_DRIFT", "NA_KEY",
           "threshold", "case_reference", "case_threshold", "negligible",
           "tors_avg", "tors_ratio", "tors_flag", "mark_na", "clear_na",
           "story_stiffness", "soft_story"]
