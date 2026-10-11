"""Tolerance comparator for analysis-results JSON (equivalence testing).

Used by the performance equivalence tests (CONTRACT "Analysis performance,
solver policy and convergence strategy") to prove that a fast path (sparse
solver, factor-once reuse, ...) reproduces the reference results.

Numbers are compared with a RELATIVE tolerance scaled per quantity family:
``|a - b| <= rtol * max(|a|, |b|, S)`` where ``S`` is the largest magnitude
of the same quantity — or of its sibling quantities (``base.FX`` ~
``base.*``) — across the reference results (the "family" is the key path
with every identifier-like dict key and every list index collapsed — e.g.
``cases.DEAD.node_disp.*.#``).  Exact zeros of round-off
size in one result vs. ~1e-20 in the other therefore compare equal, while
a genuine relative change of a quantity is caught.  Strings / bools /
structure (keys, list lengths) must match exactly.

Modal results are compared INVARIANTLY (``modal_invariant=True``, the
default): an eigenvector's sign is arbitrary in every eigen solver, so
each new mode shape (and its ``gamma_x`` / ``gamma_y``) is sign-aligned to
the reference first; and for a cluster of REPEATED eigenvalues (relative
period spread < ``degenerate_rtol``, e.g. the X/Y pair of a doubly
symmetric building) any basis of the degenerate subspace is valid, so the
cluster's individual shapes / factors are dropped and only the cluster
sums of the participation ratios ``ux`` / ``uy`` / ``rz`` (basis
invariant) are compared.
"""

from __future__ import annotations

import copy
import math
from typing import Dict, List, Tuple

# top-level result blocks keyed by case / combo / cut names: the name is
# collapsed so a quantity's scale is taken across every case (a DEAD-only
# "ux" of round-off size is judged against the real lateral ux of EQX)
NAME_KEYED = ("cases", "combos", "rs_cases", "th_cases", "pushover",
              "staged", "buckling", "takedown", "section_cuts", "piers",
              "deflection_checks", "story_stiffness", "irregularity",
              "story_props", "nonlinear_static", "steady_state", "psd",
              "hyperstatic", "convergence")


def _family(path: Tuple) -> str:
    """Quantity family of a key path: list indices -> "#", identifier-like
    keys (node tags, member uids, story / combo names: anything with a
    digit, space or sign character) and the name level of the
    :data:`NAME_KEYED` blocks -> "*"."""
    fam = []
    for i, p in enumerate(path):
        if isinstance(p, int):
            fam.append("#")
        elif (i == 1 and path[0] in NAME_KEYED) or any(
                c.isdigit() for c in p) or any(c in p for c in " -+"):
            fam.append("*")
        else:
            fam.append(p)
    return ".".join(fam)


def _walk(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, path + (str(k),))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _walk(v, path + (i,))
    else:
        yield path, obj


def _group(fam: str) -> str:
    """The sibling group of a family: its LEAF quantity key -> "*"
    (``base.FX`` -> ``base.*``; ``member_stations.*.T.#`` ->
    ``member_stations.*.*.#``).  A key followed by a name level
    (``node_disp.*.#``) is a container, not a leaf: no group (the family
    itself), so displacements are never scaled by forces."""
    parts = fam.split(".")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "*":
            return fam
        if parts[i] != "#":
            parts[i] = "*"
            break
    return ".".join(parts)


def _scales(ref) -> Dict[str, float]:
    sc: Dict[str, float] = {}
    for path, v in _walk(ref):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if math.isfinite(v):
                f = _family(path)
                a = abs(float(v))
                sc[f] = max(sc.get(f, 0.0), a)
                g = "~" + _group(f)
                sc[g] = max(sc.get(g, 0.0), a)
    return sc


def _scale(sc: Dict[str, float], path) -> float:
    f = _family(path)
    return max(sc.get(f, 0.0), sc.get("~" + _group(f), 0.0))


def _shape_dot(a: dict, b: dict) -> float:
    tot = 0.0
    for t, va in a.items():
        vb = b.get(t)
        if vb is None:
            continue
        tot += sum(float(x) * float(y) for x, y in zip(va, vb))
    return tot


def align_modal(ref: dict, new: dict, degenerate_rtol: float = 1e-6
                ) -> Tuple[dict, dict]:
    """Copies of two results dicts with the ``modal`` block made
    solver-invariant (see the module docstring)."""
    if not (isinstance(ref, dict) and isinstance(new, dict)):
        return ref, new
    ma, mb = ref.get("modal"), new.get("modal")
    if not (isinstance(ma, dict) and isinstance(mb, dict)):
        return ref, new
    ref, new = copy.copy(ref), copy.copy(new)
    ma, mb = copy.deepcopy(ma), copy.deepcopy(mb)
    ref["modal"], new["modal"] = ma, mb
    sa, sb = ma.get("shapes") or {}, mb.get("shapes") or {}
    pa, pb = ma.get("participation") or [], mb.get("participation") or []
    # 1) sign alignment
    for key, shp in sa.items():
        if key not in sb:
            continue
        if _shape_dot(shp, sb[key]) < 0.0:
            sb[key] = {t: [-float(x) for x in v] for t, v in sb[key].items()}
            i = int(key) - 1
            if 0 <= i < len(pb):
                for g in ("gamma_x", "gamma_y"):
                    if g in pb[i]:
                        pb[i][g] = -pb[i][g]
    # 2) repeated-eigenvalue clusters
    per = ma.get("periods") or []
    clusters: List[List[int]] = []
    for i, T in enumerate(per):
        if clusters and abs(T - per[clusters[-1][-1]]) <= \
                degenerate_rtol * max(abs(T), 1e-300):
            clusters[-1].append(i)
        else:
            clusters.append([i])
    for cl in clusters:
        if len(cl) < 2:
            continue
        for m in (ma, mb):
            part = m.get("participation") or []
            shp = m.get("shapes") or {}
            sums = {k: sum(float(part[i].get(k, 0.0)) for i in cl
                           if i < len(part)) for k in ("ux", "uy", "rz")}
            for j, i in enumerate(cl):
                if i < len(part):
                    keep = {k: part[i][k] for k in ("mode", "T")
                            if k in part[i]}
                    if j == 0:
                        keep.update({f"cluster_{k}": v
                                     for k, v in sums.items()})
                    part[i] = keep
                shp.pop(str(i + 1), None)
    return ref, new


def compare(ref, new, rtol: float = 1e-9, max_report: int = 20,
            ignore_keys=(), modal_invariant: bool = True
            ) -> Tuple[bool, List[str], float]:
    """``(equal, mismatches, worst_relative_error)``.

    ``ignore_keys``: dict keys whose values are skipped — for derived
    quantities that are ratios of round-off noise on symmetric models
    (e.g. the ASCE 7 ``tors_ratio_y`` of an X-only load on a doubly
    symmetric building is max/avg of ~1e-19 m drifts in BOTH runs).
    ``modal_invariant``: see :func:`align_modal`.
    """
    if modal_invariant:
        ref, new = align_modal(ref, new)
    sc = _scales(ref)
    ignore = set(ignore_keys)
    bad: List[str] = []
    worst = [0.0]

    def rec(a, b, path):
        if len(bad) >= max_report:
            return
        if isinstance(a, dict):
            if not isinstance(b, dict) or set(a) != set(b):
                ka = set(a) if isinstance(a, dict) else set()
                kb = set(b) if isinstance(b, dict) else set()
                bad.append(f"{'.'.join(map(str, path))}: keys differ "
                           f"(-{sorted(ka - kb)[:5]} +{sorted(kb - ka)[:5]})")
                return
            for k in a:
                if k in ignore:
                    continue
                rec(a[k], b[k], path + (str(k),))
            return
        if isinstance(a, (list, tuple)):
            if not isinstance(b, (list, tuple)) or len(a) != len(b):
                bad.append(f"{'.'.join(map(str, path))}: list length / type "
                           "differs")
                return
            for i, (x, y) in enumerate(zip(a, b)):
                rec(x, y, path + (i,))
            return
        num_a = isinstance(a, (int, float)) and not isinstance(a, bool)
        num_b = isinstance(b, (int, float)) and not isinstance(b, bool)
        if num_a and num_b:
            fa, fb = float(a), float(b)
            if not (math.isfinite(fa) and math.isfinite(fb)):
                if not (fa == fb or (math.isnan(fa) and math.isnan(fb))):
                    bad.append(f"{'.'.join(map(str, path))}: {a} != {b}")
                return
            s = max(abs(fa), abs(fb), _scale(sc, path))
            d = abs(fa - fb)
            if s > 0.0:
                worst[0] = max(worst[0], d / s)
            if d > rtol * s:
                bad.append(f"{'.'.join(map(str, path))}: {a!r} vs {b!r} "
                           f"(rel {d / s:.3e})")
            return
        if a != b:
            bad.append(f"{'.'.join(map(str, path))}: {a!r} != {b!r}")

    rec(ref, new, ())
    return (not bad), bad, worst[0]
