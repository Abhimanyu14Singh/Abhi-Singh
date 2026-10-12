"""Time-history shell sources for the shell contours / tables (Wave C).

Hooked into :mod:`skyframe.core.shell_results` (three one-line hooks).
A TH case run with ``th_output.shells`` (core/th_output.py) carries
``results["th_cases"][name]["shell_forces"]``::

    {"steps": [50, 120], "t": [0.25, 0.6],
     "at":  {"50": {"<quad>": [8 resultants]}, ...},   # snapshots
     "max": {"<quad>": [8]}, "min": {"<quad>": [8]}}   # step envelope

Sources:

* ``"<case>"`` — the step envelope: one max/min box per element
  (``bounded``, Max / Min rows in the tables, like an envelope combo);
  a TH member of a combo enters with the same box.
* ``"<case> @ step <k>"`` — the signed state at output step ``k``
  (t = k dt), kind ``"time_history_step"``.
"""

from __future__ import annotations

import re
from typing import List, Optional

import numpy as np

STEP_RE = re.compile(r"^(.*) @ step (\d+)$")


def step_name(case: str, step: int) -> str:
    return f"{case} @ step {int(step)}"


def _block(results: dict, name: str) -> Optional[dict]:
    cd = (results.get("th_cases") or {}).get(name)
    if not isinstance(cd, dict):
        return None
    sf = cd.get("shell_forces")
    return sf if isinstance(sf, dict) else None


def th_states(results: dict, name: str, n: int, arr, unavailable):
    """``(states, bounded)`` of the step envelope of TH case ``name``."""
    sf = _block(results, name)
    if not sf or not sf.get("max"):
        raise unavailable(f"time-history case {name!r} recorded no shell "
                          "forces (set the case's shell output request)")
    hi = arr(sf["max"], n)
    lo = arr(sf["min"], n)
    return [(np.minimum(lo, hi) + 0.0, np.maximum(lo, hi) + 0.0)], True


def step_states(model, results: dict, name: str, n: int, arr):
    """``(states, bounded)`` of a ``"<case> @ step <k>"`` source, or None
    when ``name`` is not such a source."""
    m = STEP_RE.match(name or "")
    if not m or m.group(1) not in (getattr(model, "th_cases", None) or {}):
        return None
    sf = _block(results, m.group(1))
    snap = ((sf or {}).get("at") or {}).get(str(int(m.group(2))))
    if snap is None:
        return None
    a = arr(snap, n)
    return [(a, a)], False


def pseudo_kind(model, name: str) -> Optional[str]:
    m = STEP_RE.match(name or "")
    if m and m.group(1) in (getattr(model, "th_cases", None) or {}):
        return "time_history_step"
    return None


def source_names(results: dict) -> List[str]:
    """TH envelope sources then their step snapshots (list_sources)."""
    out: List[str] = []
    for name in (results.get("th_cases") or {}):
        sf = _block(results, name)
        if not sf:
            continue
        out.append(name)
        out += [step_name(name, s) for s in sf.get("steps") or []]
    return out
