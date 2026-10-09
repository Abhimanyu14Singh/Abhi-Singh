"""Extended load-combination evaluation (ETABS Load Combinations parity).

Evaluates every combo that is NOT a legacy combo (``add``/``envelope``
over static cases only — those keep the original
``OpenSeesEngine._superpose`` / ``_envelope`` path, bit-identical).

Each member contributes, per output quantity, an interval ``(hi, lo)``:

* signed single-valued result ``x`` (static case, staged final state,
  single-valued add combo) with factor ``f``: ``hi = lo = f*x``;
* response-spectrum / RS directional result (unsigned magnitude ``r``):
  ``hi = +|f|*r``, ``lo = -|f|*r`` (RS signs are lost — ETABS behaviour);
* max/min result (envelope combo, any max/min combo, time-history
  envelope ``max``/``min`` of the signed series): ``f*max`` and ``f*min``,
  ordered so ``hi >= lo``.

Combination rules over the member intervals:

* ``add``      hi = sum(hi_i),                lo = sum(lo_i)
* ``envelope`` hi = max(hi_i),                lo = min(lo_i)
* ``abs``      V = sum(max(|hi_i|, |lo_i|)),   hi = +V, lo = -V
* ``srss``     V = sqrt(sum(max(|hi_i|,|lo_i|)^2)), hi = +V, lo = -V
* ``range``    hi = sum(max(hi_i, 0)),        lo = sum(min(lo_i, 0))

A linear-add combo whose (flattened) leaves are all signed (static /
staged) stays SINGLE-VALUED (standard case shape, no ``"min"``); every
other combo is a max/min pair in the envelope-combo shape (``minima``).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from skyframe.core.combos_ext import (  # noqa: F401  (re-exported)
    is_legacy, leaf_factors, member_kind, static_pattern_factors)

_BASE_KEYS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
_ST_KEYS = ("N", "V2", "V3", "T", "M2", "M3")
TH_WARNING = ("time-history member(s) record only story ux/uy/drift/shear "
              "and base FX/FY; the other quantities are omitted from this "
              "combo")


# --------------------------------------------------------------------------- #
# flat <-> CaseResults
# --------------------------------------------------------------------------- #
def _flatten(cr) -> Dict[tuple, np.ndarray]:
    out: Dict[tuple, np.ndarray] = {}
    for fld in ("node_disp", "reactions", "member_forces"):
        for k, v in getattr(cr, fld).items():
            out[(fld, k)] = np.asarray(v, dtype=float)
    for k in _BASE_KEYS:
        if k in cr.base:
            out[("base", k)] = np.asarray([cr.base[k]], dtype=float)
    for s, row in cr.story.items():
        for k, v in row.items():
            out[("story", s, k)] = np.asarray([v], dtype=float)
    for uid, st in cr.member_stations.items():
        for k in _ST_KEYS:
            if k in st:
                out[("st", uid, k)] = np.asarray(st[k], dtype=float)
    return out


def _stations_x(crs) -> Dict[str, List[float]]:
    xs: Dict[str, List[float]] = {}
    for cr in crs:
        for uid, st in cr.member_stations.items():
            if uid not in xs and "x" in st:
                xs[uid] = list(st["x"])
    return xs


def _unflatten(name: str, flat: Dict[tuple, np.ndarray],
               order: List[tuple], xs: Dict[str, List[float]]):
    from skyframe.engine.opensees_engine import CaseResults
    node_disp: Dict = {}
    reactions: Dict = {}
    member_forces: Dict = {}
    base: Dict[str, float] = {}
    story: Dict[str, Dict[str, float]] = {}
    stations: Dict[str, Dict[str, List[float]]] = {}
    for key in order:
        v = flat[key]
        tag = key[0]
        if tag == "node_disp":
            node_disp[key[1]] = [float(x) for x in v]
        elif tag == "reactions":
            reactions[key[1]] = [float(x) for x in v]
        elif tag == "member_forces":
            member_forces[key[1]] = [float(x) for x in v]
        elif tag == "base":
            base[key[1]] = float(v[0])
        elif tag == "story":
            story.setdefault(key[1], {})[key[2]] = float(v[0])
        elif tag == "st":
            e = stations.get(key[1])
            if e is None:
                e = stations[key[1]] = {"x": list(xs.get(key[1], []))}
            e[key[2]] = [float(x) for x in v]
    return CaseResults(name=name, node_disp=node_disp, reactions=reactions,
                       base=base, member_forces=member_forces, story=story,
                       member_stations=stations)


# --------------------------------------------------------------------------- #
# member terms
# --------------------------------------------------------------------------- #
class _Term:
    """One member's interval contribution: flat hi/lo arrays + key order."""

    __slots__ = ("hi", "lo", "order", "xs", "th")

    def __init__(self, hi, lo, order, xs, th=False):
        self.hi, self.lo, self.order, self.xs, self.th = hi, lo, order, xs, th


def _signed_term(cr, f: float) -> _Term:
    flat = _flatten(cr)
    a = {k: f * v for k, v in flat.items()}
    return _Term(a, a, list(flat), _stations_x([cr]))


def _rs_term(cr, f: float) -> _Term:
    flat = _flatten(cr)
    af = abs(f)
    hi = {k: af * np.abs(v) for k, v in flat.items()}
    lo = {k: -v for k, v in hi.items()}
    return _Term(hi, lo, list(flat), _stations_x([cr]))


def _pair_term(hi_flat, lo_flat, order, xs, f: float, th=False) -> _Term:
    hi: Dict[tuple, np.ndarray] = {}
    lo: Dict[tuple, np.ndarray] = {}
    keys = [k for k in order if k in lo_flat]
    for k in keys:
        p = f * hi_flat[k]
        q = f * lo_flat[k]
        hi[k] = np.maximum(p, q)
        lo[k] = np.minimum(p, q)
    return _Term(hi, lo, keys, xs, th)


def _envelope_cr_term(cr, f: float) -> _Term:
    hi = _flatten(cr)
    lo = _flatten(cr.minima)
    return _pair_term(hi, lo, list(hi), _stations_x([cr]), f)


def th_envelope(model, th) -> Tuple[Dict[tuple, np.ndarray],
                                    Dict[tuple, np.ndarray], List[tuple]]:
    """(max, min, key order) of a THResults over its recorded quantities.

    story ux/uy: max/min of the signed series; drift_x/drift_y: max/min of
    the signed inter-story drift-ratio series (same h rule as the TH
    peaks); shear_x/shear_y: +/- the recorded peak (the per-step story
    shear series is not stored); base FX/FY: max/min of the signed series.
    """
    hi: Dict[tuple, np.ndarray] = {}
    lo: Dict[tuple, np.ndarray] = {}
    order: List[tuple] = []
    n = len(th.t)
    prev_x = np.zeros(n)
    prev_y = np.zeros(n)
    peaks = (th.peaks or {}).get("story", {})

    def put(key, mx, mn):
        hi[key] = np.asarray([float(mx)])
        lo[key] = np.asarray([float(mn)])
        order.append(key)

    for s in model.stories:                       # bottom -> top
        if s.name not in th.story_ux:
            continue
        h = s.height if s.height > 0 else 1.0
        ux = np.asarray(th.story_ux[s.name], dtype=float)
        uy = np.asarray(th.story_uy.get(s.name, [0.0] * n), dtype=float)
        dx = (ux - prev_x) / h
        dy = (uy - prev_y) / h
        e = lambda a: (a.max(), a.min()) if a.size else (0.0, 0.0)  # noqa
        put(("story", s.name, "ux"), *e(ux))
        put(("story", s.name, "uy"), *e(uy))
        put(("story", s.name, "drift_x"), *e(dx))
        put(("story", s.name, "drift_y"), *e(dy))
        pk = peaks.get(s.name, {})
        put(("story", s.name, "shear_x"), pk.get("shear_x", 0.0),
            -pk.get("shear_x", 0.0))
        put(("story", s.name, "shear_y"), pk.get("shear_y", 0.0),
            -pk.get("shear_y", 0.0))
        prev_x, prev_y = ux, uy
    for k, ser in (("FX", th.base_FX), ("FY", th.base_FY)):
        a = np.asarray(ser, dtype=float)
        put(("base", k), a.max() if a.size else 0.0,
            a.min() if a.size else 0.0)
    return hi, lo, order


# --------------------------------------------------------------------------- #
# combination
# --------------------------------------------------------------------------- #
def _common_order(terms: List[_Term]) -> List[tuple]:
    """Key order of the first term carrying the most keys, restricted to
    the keys every term records (TH members drop the unrecorded ones)."""
    ref = max(terms, key=lambda t: len(t.order))
    common = set(ref.order)
    for t in terms:
        common &= set(t.hi)
    return [k for k in ref.order if k in common]


def combine_terms(ctype: str, terms: List[_Term]):
    """Return (hi_flat, lo_flat, order, xs) of the combined interval."""
    order = _common_order(terms)
    xs: Dict[str, List[float]] = {}
    for t in terms:
        for u, x in t.xs.items():
            xs.setdefault(u, x)
    hi: Dict[tuple, np.ndarray] = {}
    lo: Dict[tuple, np.ndarray] = {}
    for k in order:
        his = [t.hi[k] for t in terms]
        los = [t.lo[k] for t in terms]
        if ctype == "add":
            h = his[0].copy()
            lw = los[0].copy()
            for a, b in zip(his[1:], los[1:]):
                h = h + a
                lw = lw + b
        elif ctype == "envelope":
            h = his[0].copy()
            lw = los[0].copy()
            for a, b in zip(his[1:], los[1:]):
                h = np.maximum(h, a)
                lw = np.minimum(lw, b)
        elif ctype in ("abs", "srss"):
            mags = [np.maximum(np.abs(a), np.abs(b))
                    for a, b in zip(his, los)]
            if ctype == "abs":
                v = mags[0].copy()
                for m in mags[1:]:
                    v = v + m
            else:
                v = mags[0] ** 2
                for m in mags[1:]:
                    v = v + m ** 2
                v = np.sqrt(v)
            h, lw = v, -v
        elif ctype == "range":
            h = np.zeros_like(his[0])
            lw = np.zeros_like(los[0])
            for a, b in zip(his, los):
                h = h + np.maximum(a, 0.0)
                lw = lw + np.minimum(b, 0.0)
        else:                                   # pragma: no cover
            raise ValueError(f"unknown combo_type {ctype!r}")
        hi[k] = h
        lo[k] = lw + 0.0                        # normalise -0.0
    return hi, lo, order, xs


def superpose_signed(name: str, parts) -> "CaseResults":
    """Single-valued linear superposition of signed CaseResults parts
    ``[(cr, f), ...]`` (static and/or staged final states): every field
    recorded by ALL parts is summed (node_disp, reactions, base,
    member_forces, story, member_stations, member_deflections,
    shell_forces, shell_nodal)."""
    from skyframe.engine.opensees_engine import CaseResults
    p0 = parts[0][0]

    def vecs(get) -> dict:
        out = {}
        for k, v in get(p0).items():
            if not all(k in get(r) for r, _ in parts):
                continue
            acc = [0.0] * len(v)
            for r, f in parts:
                for i, x in enumerate(get(r)[k]):
                    acc[i] += f * x
            out[k] = acc
        return out

    base = {k: sum(f * r.base[k] for r, f in parts) for k in _BASE_KEYS
            if all(k in r.base for r, _ in parts)}
    story = {}
    for s, row in p0.story.items():
        if all(s in r.story for r, _ in parts):
            story[s] = {k: sum(f * r.story[s][k] for r, f in parts)
                        for k in row}
    stations = {}
    for uid, st in p0.member_stations.items():
        if not all(uid in r.member_stations for r, _ in parts):
            continue
        e = {"x": list(st["x"])}
        for key in _ST_KEYS:
            acc = [0.0] * len(st[key])
            for r, f in parts:
                for i, x in enumerate(r.member_stations[uid][key]):
                    acc[i] += f * x
            e[key] = acc
        stations[uid] = e
    defl = {}
    for uid, md in p0.member_deflections.items():
        if not all(uid in r.member_deflections for r, _ in parts):
            continue
        e = {"x": list(md["x"])}
        for key in ("dy", "dz"):
            acc = [0.0] * len(md[key])
            for r, f in parts:
                for i, x in enumerate(r.member_deflections[uid][key]):
                    acc[i] += f * x
            e[key] = acc
        defl[uid] = e
    return CaseResults(
        name=name,
        node_disp=vecs(lambda r: r.node_disp),
        reactions=vecs(lambda r: r.reactions),
        base=base,
        member_forces=vecs(lambda r: r.member_forces),
        story=story,
        member_stations=stations,
        shell_forces=vecs(lambda r: r.shell_forces),
        shell_nodal=vecs(lambda r: r.shell_nodal),
        member_deflections=defl,
    )


class ComboEvaluator:
    """Evaluates the extended combos of one ``run()`` (memoised, nested)."""

    def __init__(self, engine, model, cases, rs_cases, th_cases, staged,
                 combos, combo_status, notes):
        self.engine = engine
        self.model = model
        self.cases = cases
        self.rs_cases = rs_cases
        self.th_cases = th_cases
        self.staged = staged
        self.combos = combos
        self.combo_status = combo_status
        self.notes = notes

    # -- availability -----------------------------------------------------
    def _available(self, name: str) -> bool:
        kind = member_kind(self.model, name)
        if kind == "static":
            return name in self.cases
        if kind in ("response_spectrum", "rs_directional"):
            return name in self.rs_cases
        if kind == "time_history":
            return name in self.th_cases
        if kind == "staged":
            return name in self.staged
        if kind == "combo":
            return self.evaluate(name) is not None
        return False

    # -- public -------------------------------------------------------------
    def evaluate(self, name: str):
        """CaseResults of combo ``name`` (None when skipped); records
        ``combo_status`` / skip notes."""
        if name in self.combos:
            return self.combos[name]
        if self.combo_status.get(name) == "skipped":
            return None
        cb = self.model.combos[name]
        if is_legacy(self.model, cb):        # legacy combo already handled
            return None
        missing = [c for c in cb.cases if not self._available(c)]
        if missing:
            self.combo_status[name] = "skipped"
            self.notes.append(f"combo {name!r} skipped: member(s) "
                              f"{missing} not run")
            return None
        res = self._compute(name, cb)
        self.combos[name] = res
        self.combo_status[name] = "finished"
        return res

    # -- internals ------------------------------------------------------------
    def _signed_result(self, name: str):
        kind = member_kind(self.model, name)
        if kind == "static":
            return self.cases[name]
        if kind == "staged":
            return self.staged[name].case
        return None

    def _term(self, name: str, f: float) -> _Term:
        model = self.model
        kind = member_kind(model, name)
        if kind in ("static", "staged"):
            return _signed_term(self._signed_result(name), f)
        if kind in ("response_spectrum", "rs_directional"):
            return _rs_term(self.rs_cases[name], f)
        if kind == "time_history":
            hi, lo, order = th_envelope(model, self.th_cases[name])
            return _pair_term(hi, lo, order, {}, f, th=True)
        # nested combo
        res = self.evaluate(name)
        if res.minima is None:
            return _signed_term(res, f)
        t = _envelope_cr_term(res, f)
        t.th = TH_WARNING in (res.warning or "")
        return t

    def _compute(self, name: str, cb):
        model = self.model
        ctype = cb.combo_type
        if ctype == "add":
            leaves = leaf_factors(model, name)
            if all(member_kind(model, c) in ("static", "staged")
                   for c in leaves):
                if all(c in model.cases for c in leaves):
                    # nested static-only add: the exact legacy superposition
                    return self.engine._superpose(name, leaves)
                return superpose_signed(
                    name, [(self._signed_result(c), f)
                           for c, f in leaves.items()])
            members = list(leaves.items())
        else:
            members = list(cb.cases.items())
        terms = [self._term(c, f) for c, f in members]
        hi, lo, order, xs = combine_terms(ctype, terms)
        res = _unflatten(name, hi, order, xs)
        res.minima = _unflatten(name + " (min)", lo, order, xs)
        if any(t.th for t in terms):
            res.warning = TH_WARNING
        return res


def run_extended(engine, model, cases, rs_cases, th_cases, staged, combos,
                 combo_status, notes) -> None:
    """Evaluate every non-legacy combo of ``model`` into ``combos`` /
    ``combo_status`` (in place), then restore model order."""
    ev = ComboEvaluator(engine, model, cases, rs_cases, th_cases, staged,
                        combos, combo_status, notes)
    for name, cb in model.combos.items():
        if not is_legacy(model, cb):
            ev.evaluate(name)
