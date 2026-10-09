"""Extended load combinations (ETABS Define > Load Combinations parity).

Model-side helpers for :class:`skyframe.core.model.LoadCombo`:

* member classification — a combo member may be a static case, a
  response-spectrum case, an RS directional combo (``model.rs_combos``), a
  time-history case, a staged-construction case (its accumulated final
  state) or another combo (nesting).  Pushover, buckling, steady-state,
  PSD and the reserved MODAL case are rejected with a clear error;
* cycle detection over nested combos;
* flattening of nested LINEAR-ADD combos into leaf factors;
* the "legacy" test (``add``/``envelope`` over static cases only) that
  keeps the pre-existing engine path — and therefore byte-identical
  results — for every combo that was valid before this feature.

The numerical evaluation lives in :mod:`skyframe.engine.combos_ext`.
See CONTRACT "Load combinations: RS/TH/nested members and ABS/SRSS/Range
types".
"""

from __future__ import annotations

from typing import Dict, List, Optional

#: every accepted ``LoadCombo.combo_type``
COMBO_TYPES_EXT = ("add", "envelope", "abs", "srss", "range")

#: member kinds that may enter a combo
MEMBER_KINDS = ("static", "response_spectrum", "rs_directional",
                "time_history", "staged", "combo")

_REJECT = {
    "pushover": ("nonlinear static pushover results are a capacity curve, "
                 "not a superposable load state"),
    "buckling": "buckling results are eigenvalues, not a load state",
    "steady_state": ("steady-state results are complex frequency-domain "
                     "amplitudes and cannot be combined"),
    "psd": ("power-spectral-density results are statistical responses and "
            "cannot be combined"),
    "modal": "modal results are mode shapes, not a load state",
}


def member_kind(model, name: str) -> Optional[str]:
    """Kind of a combo member name, or None if it is not defined.

    Lookup order (first match wins): static case, RS case, RS directional
    combo, TH case, staged case, combo, then the rejected kinds."""
    if name in model.cases:
        return "static"
    if name in model.rs_cases:
        return "response_spectrum"
    if name in (getattr(model, "rs_combos", None) or {}):
        return "rs_directional"
    if name in model.th_cases:
        return "time_history"
    if name in model.staged_cases:
        return "staged"
    if name in model.combos:
        return "combo"
    for kind, attr in (("pushover", "pushover_cases"),
                       ("buckling", "buckling_cases"),
                       ("steady_state", "steady_state_cases"),
                       ("psd", "psd_cases")):
        if name in (getattr(model, attr, None) or {}):
            return kind
    from skyframe.core.model import MODAL_CASE
    if name == MODAL_CASE:
        return "modal"
    return None


def find_cycle(model, extra=None) -> Optional[List[str]]:
    """A combo-reference cycle as a name path ``[a, b, ..., a]``, else None.

    ``extra`` (a LoadCombo not yet stored) is considered in place of any
    same-named stored combo."""
    graph: Dict[str, List[str]] = {n: list(cb.cases)
                                   for n, cb in model.combos.items()}
    if extra is not None:
        graph[extra.name] = list(extra.cases)
    combo_names = set(graph)

    # a member name resolves to a combo only when it is not a case first
    def is_combo(n: str) -> bool:
        return n in combo_names and member_kind_no_combo(model, n) is None

    WHITE, GREY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def dfs(n: str, path: List[str]) -> Optional[List[str]]:
        color[n] = GREY
        path.append(n)
        for m in graph[n]:
            if not is_combo(m):
                continue
            if color[m] == GREY:
                return path[path.index(m):] + [m]
            if color[m] == WHITE:
                cyc = dfs(m, path)
                if cyc:
                    return cyc
        path.pop()
        color[n] = BLACK
        return None

    for n in graph:
        if color[n] == WHITE:
            cyc = dfs(n, [])
            if cyc:
                return cyc
    return None


def member_kind_no_combo(model, name: str) -> Optional[str]:
    """:func:`member_kind` restricted to the non-combo kinds that take
    precedence over a same-named combo (static/RS/RS-dir/TH/staged)."""
    k = member_kind(model, name)
    return k if k in MEMBER_KINDS and k != "combo" else None


def validate_combo(model, combo) -> None:
    """Raise ValueError for an invalid combo (type, members, cycles)."""
    if combo.combo_type not in COMBO_TYPES_EXT:
        raise ValueError(f"Combo {combo.name}: combo_type must be "
                         f"{'|'.join(COMBO_TYPES_EXT)}, got "
                         f"{combo.combo_type!r}")
    if combo.combo_type != "add" and not combo.cases:
        raise ValueError(f"Combo {combo.name}: an {combo.combo_type} combo "
                         "needs at least one case")
    for c, f in combo.cases.items():
        kind = member_kind(model, c)
        if kind is None:
            if c == combo.name:
                raise ValueError(f"Combo {combo.name}: a combo cannot "
                                 "reference itself (cycle)")
            raise ValueError(f"Combo {combo.name}: unknown case {c}")
        if kind in _REJECT:
            raise ValueError(f"Combo {combo.name}: {kind} case {c!r} cannot "
                             f"enter a load combo ({_REJECT[kind]})")
        try:
            ok = float(f) == float(f) and abs(float(f)) != float("inf")
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise ValueError(f"Combo {combo.name}: factor of {c!r} must be a "
                             f"finite number, got {f!r}")
    if any(member_kind(model, c) in (None, "combo") for c in combo.cases) \
            or combo.name in combo.cases:
        cyc = find_cycle(model, extra=combo)
        if cyc:
            raise ValueError(f"Combo {combo.name}: circular combo reference "
                             f"{' -> '.join(cyc)}")


def is_legacy(model, combo) -> bool:
    """True for the pre-existing combo forms (``add``/``envelope`` over
    static cases only), which keep the original engine path."""
    return (combo.combo_type in ("add", "envelope")
            and all(c in model.cases for c in combo.cases))


def leaf_factors(model, name: str) -> Optional[Dict[str, float]]:
    """Flatten a LINEAR-ADD combo through nested linear-add combos.

    Returns ``{leaf member: total factor}`` in first-appearance order,
    where a leaf is any member that is NOT a nested ``add`` combo (static
    / staged / RS / TH / non-add combo).  None when ``name`` is not an
    ``add`` combo.  Exact for max/min (interval) arithmetic too: scaling
    and adding intervals commutes with flattening."""
    cb = model.combos.get(name)
    if cb is None or cb.combo_type != "add" or \
            member_kind_no_combo(model, name) is not None:
        return None
    out: Dict[str, float] = {}

    def walk(cname: str, scale: float, depth: int) -> None:
        if depth > len(model.combos) + 1:          # defensive (cycles)
            raise ValueError(f"Combo {name}: circular combo reference")
        for c, f in model.combos[cname].cases.items():
            sub = model.combos.get(c)
            if (member_kind(model, c) == "combo" and sub is not None
                    and sub.combo_type == "add"):
                walk(c, scale * f, depth + 1)
            else:
                fac = f if scale == 1.0 else scale * f
                out[c] = out[c] + fac if c in out else fac

    walk(name, 1.0, 0)
    return out


def is_single_valued(model, name: str) -> bool:
    """True when the combo result is a single signed state (no max/min):
    a linear-add combo whose flattened leaves are all static or staged."""
    leaves = leaf_factors(model, name)
    if leaves is None:
        return False
    return all(member_kind(model, c) in ("static", "staged") for c in leaves)


def static_pattern_factors(model, name: str) -> Optional[Dict[str, float]]:
    """Effective pattern factors of a single-valued add combo whose leaves
    are all static cases (for the gravity takedown); else None."""
    leaves = leaf_factors(model, name)
    if leaves is None or not all(c in model.cases for c in leaves):
        return None
    eff: Dict[str, float] = {}
    for base_case, f in leaves.items():
        for p, pf in model.cases[base_case].patterns.items():
            eff[p] = eff.get(p, 0.0) + f * pf
    return eff


def all_members(model, name: str) -> List[str]:
    """Every (recursive) non-combo member name of a combo, de-duplicated."""
    seen: List[str] = []
    stack = [name]
    visited = set()
    while stack:
        n = stack.pop()
        if n in visited:
            continue
        visited.add(n)
        for c in model.combos[n].cases:
            if member_kind(model, c) == "combo":
                stack.append(c)
            elif c not in seen:
                seen.append(c)
    return seen
