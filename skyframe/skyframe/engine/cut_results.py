"""Section-cut, pier and spandrel forces for EVERY case type (Wave C).

The legacy post-processing (``compute_section_cut``, ``_compute_piers``,
``engine/spandrels.compute``) covers static cases and single-valued
(additive) combos.  This module extends the SAME three kernels to:

* **response spectrum** (and RS directional combos) — per mode the signed
  cut values are evaluated on the mode's static solve and carried through
  the RS machinery as one pseudo member-station entry (uid
  :data:`PSEUDO`); every modal / directional / eccentricity rule therefore
  combines them exactly like any other response quantity, and
  :func:`harvest_rs` lifts them out of the RS results again (the pseudo
  entry never reaches the results JSON).  Positive envelopes.
* **time history** (direct integration) — a per-step recorder
  (:class:`THRecorder`) evaluates the kernels on the step's element forces
  (only the members / shell elements a cut touches are queried).  The full
  history goes to ``th_cases[name]["cut_forces"]``; the signed max / min
  peaks to the standard blocks (``"min"`` sub-dict).
* **nonlinear static** — per converged step (``nonlinear.history
  ["cut_forces"]``); the final state enters the standard blocks.
* **max/min combos** (legacy envelope combos and every extended combo
  that is not single-valued) — the interval rules of CONTRACT "Load
  combinations" applied to the member vectors (via
  :func:`skyframe.engine.combos_ext.combine_terms`).
* **staged** cases — the accumulated final state.

The kernels are linear in the element forces, so the per-mode / per-step
evaluation is exact.  Results land in the existing ``section_cuts`` /
``piers`` / ``spandrels`` blocks with the existing per-entry shape; entries
that are max/min pairs carry the maxima in the standard keys plus a
``"min"`` sub-dict of the same shape.  Static cases and single-valued
combos keep their legacy entries bit-identically (never recomputed here).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

PSEUDO = "__cutx__"          # pseudo member-station uid (RS carrier)
CUT_KEYS = ("FX", "FY", "FZ", "MX", "MY", "MZ")
PIER_KEYS = ("P", "V", "M")
SPD_KEYS = ("P", "V2", "V3", "T", "M2", "M3")
ST_KEYS = ("N", "V2", "V3", "T", "M2", "M3")


# --------------------------------------------------------------------------- #
# geometry (case independent, cached per engine)
# --------------------------------------------------------------------------- #
def enabled(model) -> bool:
    from skyframe.core import spandrels as _spd
    auto = getattr(model, "auto_pier_walls", False)
    return bool(getattr(model, "section_cuts", None)) or any(
        r.kind == "wall" and (getattr(r, "pier", "") or auto)
        for r in model.shells) or _spd.enabled(model)


class Geometry:
    """Every cut of the model in a fixed order + the flat key layout."""

    def __init__(self, eng, asm):
        import warnings
        from skyframe.engine.opensees_engine import (
            _SECTION_AXIS_INDEX, _CUT_TOL, _local_axes, _shell_crosses,
            _within_ranges)
        from skyframe.engine import spandrels as _spd_eng
        model = eng.model
        self.keys: List[tuple] = []
        self.members: Dict[str, object] = {}     # uid -> member (stations)
        self.quads: set = set()                 # shell indices (nodal)
        # -- section cuts (the compute_section_cut filter, verbatim)
        self.cuts = []
        for cut in (getattr(model, "section_cuts", None) or []):
            ai = _SECTION_AXIS_INDEX[cut.axis]
            c = float(cut.coord)
            cm, cs = model.members, model.shells
            if getattr(cut, "group", None) is not None:
                from skyframe.core.groups import resolve_group
                g = resolve_group(model, cut.group)
                cm, cs = g["members"], g["shells"]
            cross = []
            for m in cm:
                ci, cj = m.pi[ai], m.pj[ai]
                if abs(cj - ci) <= _CUT_TOL:
                    continue
                lo, hi = (ci, cj) if ci <= cj else (cj, ci)
                if not (lo - _CUT_TOL <= c <= hi + _CUT_TOL):
                    continue
                frac = min(max((c - ci) / (cj - ci), 0.0), 1.0)
                r = tuple(m.pi[k] + frac * (m.pj[k] - m.pi[k])
                          for k in range(3))
                if not _within_ranges(r, cut):
                    continue
                xax, yax, zax, _, _ = _local_axes(m)
                sign = -1.0 if (cj - ci) > 0 else 1.0
                cross.append((m.uid, frac * m.length, r, xax, yax, zax,
                              sign))
                self.members[m.uid] = m
            n_sh = sum(1 for rg in cs if _shell_crosses(rg, cut))
            warns = ([f"{n_sh} shell(s) cross this cut but are EXCLUDED "
                      "from the resultant (v0.13 integrates frame members "
                      "only)"] if n_sh else [])
            self.cuts.append({"name": cut.name, "cross": cross,
                              "n_shells": int(n_sh), "warnings": warns})
            self.keys += [("cut", cut.name, k) for k in CUT_KEYS]
        # -- piers (engine._pier_cuts; slots in _compute_piers order)
        self.pier_cuts = []
        self.pier_slots: Dict[Tuple[str, str], int] = {}
        if eng._piers_enabled():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.pier_cuts = eng._pier_cuts(asm)
            for pc in self.pier_cuts:
                key = (pc["label"], pc["story"])
                if key not in self.pier_slots:
                    self.pier_slots[key] = len(self.keys)
                    self.keys += [("pier", key[0], key[1], k)
                                  for k in PIER_KEYS]
                for qi, _ks, _pts in pc["elems"]:
                    self.quads.add(qi)
        # -- spandrels (engine/spandrels.spandrel_groups)
        self.spd = []
        if _spd_eng.enabled(model):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.spd = _spd_eng.spandrel_groups(eng, asm)
            for g in self.spd:
                for side, cut in g["cuts"].items():
                    cut["side"] = side
                    for qi, _ks, _pts in cut["elems"]:
                        self.quads.add(qi)
                    for m, *_rest in cut["frames"]:
                        self.members[m.uid] = m
                for side in ("Left", "Right"):
                    self.keys += [("spd", g["label"], g["story"], side, k)
                                  for k in SPD_KEYS]
        self.n = len(self.keys)
        self.needs_nodal = bool(self.pier_cuts or self.spd)

    # -- kernels ---------------------------------------------------------------
    def values(self, stations: Dict[str, dict],
               nodal: Dict[int, List[float]]) -> np.ndarray:
        """Flat signed cut vector of one signed state."""
        from skyframe.engine.opensees_engine import (OpenSeesEngine,
                                                     _interp_station)
        from skyframe.engine import spandrels as _spd_eng
        out = np.zeros(self.n)
        pos = 0
        for cut in self.cuts:                    # compute_section_cut
            contribs = []
            for uid, x, r, xax, yax, zax, sign in cut["cross"]:
                st = stations.get(uid)
                if not st or not st.get("x"):
                    continue
                N = _interp_station(st, x, "N")
                V2 = _interp_station(st, x, "V2")
                V3 = _interp_station(st, x, "V3")
                T = _interp_station(st, x, "T")
                M2 = _interp_station(st, x, "M2")
                M3 = _interp_station(st, x, "M3")
                F = tuple(N * xax[k] + V2 * yax[k] + V3 * zax[k]
                          for k in range(3))
                Mv = tuple(T * xax[k] + M2 * yax[k] + M3 * zax[k]
                           for k in range(3))
                contribs.append((r, tuple(sign * v for v in F),
                                 tuple(sign * v for v in Mv)))
            if contribs:
                cen = tuple(sum(rc[k] for rc, _, _ in contribs)
                            / len(contribs) for k in range(3))
            else:
                cen = (0.0, 0.0, 0.0)
            FX = FY = FZ = MX = MY = MZ = 0.0
            for r, F, Mv in contribs:
                FX += F[0]; FY += F[1]; FZ += F[2]          # noqa: E702
                d = (r[0] - cen[0], r[1] - cen[1], r[2] - cen[2])
                MX += Mv[0] + d[1] * F[2] - d[2] * F[1]
                MY += Mv[1] + d[2] * F[0] - d[0] * F[2]
                MZ += Mv[2] + d[0] * F[1] - d[1] * F[0]
            out[pos:pos + 6] = (FX, FY, FZ, MX, MY, MZ)
            pos += 6
        acc: Dict[int, List[float]] = {}
        for pc in self.pier_cuts:                # _compute_piers
            P, V, M = OpenSeesEngine._pier_cut_forces(pc, nodal)
            slot = self.pier_slots[(pc["label"], pc["story"])]
            e = acc.setdefault(slot, [0.0, 0.0, 0.0])
            e[0] += P
            e[1] += V
            e[2] += M
        for slot, e in acc.items():
            out[slot:slot + 3] = e
        if self.pier_slots:
            pos = max(self.pier_slots.values()) + 3
        cr = SimpleNamespace(shell_nodal=nodal, member_stations=stations)
        for g in self.spd:                       # spandrels.compute
            for side in ("Left", "Right"):
                R, MR = _spd_eng._cut_resultant(g["cuts"][side], cr)
                F = R if side == "Right" else -R
                Mm = MR if side == "Right" else -MR
                out[pos:pos + 6] = (F @ g["e1"], F @ g["e2"], F @ g["e3"],
                                    Mm @ g["e1"], Mm @ g["e2"], Mm @ g["e3"])
                pos += 6
        return out

    # -- flat vector -> result blocks -----------------------------------------
    def blocks(self, hi: np.ndarray, lo: Optional[np.ndarray] = None
               ) -> Tuple[dict, dict, dict]:
        """(section_cuts entry, piers entry, spandrels entry) of one case;
        ``lo`` given -> max/min pair (``"min"`` sub-dicts)."""
        def num(a, i):
            return float(a[i]) + 0.0
        cuts: Dict[str, dict] = {}
        pos = 0
        for cut in self.cuts:
            e = {k: num(hi, pos + j) for j, k in enumerate(CUT_KEYS)}
            if lo is not None:
                e["min"] = {k: num(lo, pos + j)
                            for j, k in enumerate(CUT_KEYS)}
            e["n_members"] = len(cut["cross"])
            e["n_shells"] = cut["n_shells"]
            e["warnings"] = list(cut["warnings"])
            cuts[cut["name"]] = e
            pos += 6
        piers: Dict[str, Dict[str, dict]] = {}
        for (lab, story), slot in self.pier_slots.items():
            e = {k: num(hi, slot + j) for j, k in enumerate(PIER_KEYS)}
            if lo is not None:
                e["min"] = {k: num(lo, slot + j)
                            for j, k in enumerate(PIER_KEYS)}
            piers.setdefault(lab, {})[story] = e
        if self.pier_slots:
            pos = max(self.pier_slots.values()) + 3
        spd: Dict[str, Dict[str, dict]] = {}
        for g in self.spd:
            e = {"length": float(g["length"]), "n_shells": g["n_shells"],
                 "n_frames": g["n_frames"]}
            mn = {}
            for side in ("Left", "Right"):
                e[side] = {k: num(hi, pos + j) for j, k in enumerate(SPD_KEYS)}
                if lo is not None:
                    mn[side] = {k: num(lo, pos + j)
                                for j, k in enumerate(SPD_KEYS)}
                pos += 6
            if lo is not None:
                e["min"] = mn
            spd.setdefault(g["label"], {})[g["story"]] = e
        return cuts, piers, spd

    def series(self, rows: np.ndarray) -> dict:
        """History block of a (steps x n) matrix."""
        cols = rows.T if rows.size else np.zeros((self.n, 0))

        def col(i):
            return [float(v) + 0.0 for v in cols[i]]
        out: dict = {}
        pos = 0
        if self.cuts:
            out["section_cuts"] = {}
            for cut in self.cuts:
                out["section_cuts"][cut["name"]] = {
                    k: col(pos + j) for j, k in enumerate(CUT_KEYS)}
                pos += 6
        if self.pier_slots:
            out["piers"] = {}
            for (lab, story), slot in self.pier_slots.items():
                out["piers"].setdefault(lab, {})[story] = {
                    k: col(slot + j) for j, k in enumerate(PIER_KEYS)}
            pos = max(self.pier_slots.values()) + 3
        if self.spd:
            out["spandrels"] = {}
            for g in self.spd:
                e = {}
                for side in ("Left", "Right"):
                    e[side] = {k: col(pos + j)
                               for j, k in enumerate(SPD_KEYS)}
                    pos += 6
                out["spandrels"].setdefault(g["label"], {})[g["story"]] = e
        return out

    def from_series(self, block: dict) -> np.ndarray:
        """(steps x n) matrix of a :meth:`series` block."""
        cols: List[List[float]] = []
        for key in self.keys:
            if key[0] == "cut":
                cols.append(block["section_cuts"][key[1]][key[2]])
            elif key[0] == "pier":
                cols.append(block["piers"][key[1]][key[2]][key[3]])
            else:
                cols.append(block["spandrels"][key[1]][key[2]][key[3]]
                            [key[4]])
        return np.asarray(cols, dtype=float).T


def geometry(eng, asm) -> Optional[Geometry]:
    """The engine's cached :class:`Geometry` (None when nothing to cut)."""
    if not enabled(eng.model):
        return None
    sig = (len(asm.shell_quads), len(eng.model.members),
           len(getattr(eng.model, "section_cuts", None) or []))
    rec = eng.__dict__.get("_cutx_geo")
    if rec is None or rec[0] != sig:
        rec = (sig, Geometry(eng, asm))
        eng.__dict__["_cutx_geo"] = rec
    g = rec[1]
    return g if g.n else None


# --------------------------------------------------------------------------- #
# live element queries (subset of _member_outputs / _shell_nodal)
# --------------------------------------------------------------------------- #
def live_nodal(asm, quads) -> Dict[int, List[float]]:
    out: Dict[int, List[float]] = {}
    for qi in quads:
        vals = ops.eleResponse(asm.quad_ele[qi], "forces")
        if len(vals) == 6 * len(asm.shell_quads[qi]["nodes"]):
            out[qi] = [float(v) for v in vals]
    return out


def live_stations(eng, asm, members, recs=None, fef=None
                  ) -> Dict[str, dict]:
    """``_member_outputs`` station block for ``members`` only (same
    arithmetic); ``recs`` / ``fef`` override the engine's span-load
    records (nonlinear static steps)."""
    from skyframe.engine.opensees_engine import _section_forces
    recs = eng._seg_span_loads if recs is None else recs
    fef = eng._seg_fef if fef is None else fef
    out: Dict[str, dict] = {}
    for m in members:
        segs = asm.mesh.segments[m.uid]
        xs = eng._station_xs(m, segs)
        if m.uid in asm.truss_uids:
            N = float(ops.eleResponse(asm.seg_ele[(m.uid, 0)],
                                      "basicForce")[0])
            z = [0.0] * len(xs)
            out[m.uid] = {"x": xs, "N": [N] * len(xs), "V2": list(z),
                          "V3": list(z), "T": list(z), "M2": list(z),
                          "M3": list(z)}
            continue
        corrected: Dict[int, List[float]] = {}
        for seg in segs:
            f = ops.eleResponse(asm.seg_ele[(m.uid, seg.index)],
                                "localForce")
            c = fef.get((m.uid, seg.index))
            corrected[seg.index] = ([float(v + w) for v, w in zip(f, c)]
                                    if c is not None
                                    else [float(v) for v in f])
        cols: Dict[str, List[float]] = {k: [] for k in ST_KEYS}
        for x in xs:
            seg = segs[0] if len(segs) == 1 else None
            if seg is None:
                for s in segs:
                    if s.x0 - 1e-9 <= x <= s.x0 + s.length + 1e-9:
                        seg = s
                        break
                if seg is None:
                    seg = segs[-1]
            xi = min(max(x - seg.x0, 0.0), seg.length)
            vals = _section_forces(corrected[seg.index][:6],
                                   recs.get((m.uid, seg.index), ()), xi)
            for k, v in zip(ST_KEYS, vals):
                cols[k].append(float(v))
        out[m.uid] = {"x": xs, **cols}
    return out


def live_values(eng, asm, geo: Geometry, recs=None, fef=None) -> np.ndarray:
    st = live_stations(eng, asm, list(geo.members.values()), recs, fef)
    nodal = live_nodal(asm, sorted(geo.quads)) if geo.needs_nodal else {}
    return geo.values(st, nodal)


# --------------------------------------------------------------------------- #
# response spectrum: pseudo station carried through the RS machinery
# --------------------------------------------------------------------------- #
def _pseudo(vec: np.ndarray) -> dict:
    return {"x": [], "N": [float(v) for v in vec], "V2": [], "V3": [],
            "T": [], "M2": [], "M3": []}


def attach_mode(eng, asm, cr) -> None:
    """``_modal_static`` hook: the mode's signed cut vector as the pseudo
    member-station entry (no-op without cuts / piers / spandrels)."""
    geo = geometry(eng, asm)
    if geo is None:
        return
    nodal = live_nodal(asm, sorted(geo.quads)) if geo.needs_nodal else {}
    cr.member_stations[PSEUDO] = _pseudo(geo.values(cr.member_stations,
                                                    nodal))


def attach_unit(eng, asm, unit: dict) -> None:
    """``modal_condensed._unit_response`` hook (condensed RS path)."""
    geo = geometry(eng, asm)
    if geo is None:
        return
    nodal = live_nodal(asm, sorted(geo.quads)) if geo.needs_nodal else {}
    unit["member_stations"][PSEUDO] = _pseudo(
        geo.values(unit["member_stations"], nodal))


def harvest_rs(eng, rs_cases: Dict[str, object]) -> None:
    """``run()`` hook after the RS cases / RS combos: move every RS
    result's pseudo entry into ``eng._cutx_rs`` (positive envelopes)."""
    store = eng.__dict__.setdefault("_cutx_rs", {})
    for name, cr in rs_cases.items():
        st = cr.member_stations.pop(PSEUDO, None)
        if cr.minima is not None:
            cr.minima.member_stations.pop(PSEUDO, None)
        if st is not None:
            store[name] = np.abs(np.asarray(st["N"], dtype=float))


# --------------------------------------------------------------------------- #
# time history recorder (direct integration)
# --------------------------------------------------------------------------- #
class THRecorder:
    """Per-step cut forces (+ optional shell forces) of one TH case."""

    def __init__(self, eng, asm, th, geo: Optional[Geometry], shells,
                 frames=()):
        self.eng, self.asm, self.geo = eng, asm, geo
        self.rows: List[np.ndarray] = []
        self.frames = set(frames)                # animation frame steps
        self.fr_disp: Dict[str, dict] = {}
        if self.frames:
            self.fr_base = {t: list(ops.nodeDisp(t)) for t in asm.node_coords}
        self.base = live_values(eng, asm, geo) if geo is not None else None
        self.shells = shells                     # normalized th_output
        if shells is not None:
            self.sh_base = eng._shell_outputs(asm)
            self.sh_steps = set(shells.get("steps") or [])
            self.sh_every = int(shells.get("every") or 0)
            self.sh_at: Dict[str, dict] = {}
            self.sh_hi: Dict[int, np.ndarray] = {}
            self.sh_lo: Dict[int, np.ndarray] = {}
            self.sh_hi_k: Dict[int, List[int]] = {}

    def record(self, k: int) -> None:
        if (k + 1) in self.frames:
            out = {}
            for t, b in self.fr_base.items():
                u = ops.nodeDisp(t)
                out[str(t)] = [float(u[i] - b[i]) + 0.0
                               for i in range(min(6, len(u)))]
            self.fr_disp[str(k + 1)] = out
        if self.geo is not None:
            self.rows.append(live_values(self.eng, self.asm, self.geo)
                             - self.base)
        if self.shells is not None:
            step = k + 1
            sf = self.eng._shell_outputs(self.asm, baseline=self.sh_base)
            for qi, v in sf.items():
                a = np.asarray(v, dtype=float)
                hi = self.sh_hi.get(qi)
                if hi is None:
                    self.sh_hi[qi] = a.copy()
                    self.sh_lo[qi] = a.copy()
                else:
                    np.maximum(hi, a, out=hi)
                    np.minimum(self.sh_lo[qi], a, out=self.sh_lo[qi])
            if step in self.sh_steps or (
                    self.sh_every and step % self.sh_every == 0):
                self.sh_at[str(step)] = {str(qi): [float(x) for x in v]
                                         for qi, v in sf.items()}

    def finish(self, result) -> None:
        extra = dict(result.extra)
        if self.geo is not None:
            mat = (np.vstack(self.rows) if self.rows
                   else np.zeros((0, self.geo.n)))
            blk = self.geo.series(mat)
            blk["t"] = list(result.t)
            extra["cut_forces"] = blk
        if self.shells is not None:
            steps = sorted(int(s) for s in self.sh_at)
            extra["shell_forces"] = {
                "steps": steps,
                "t": [float(result.t[s - 1]) for s in steps
                      if 0 < s <= len(result.t)],
                "at": {str(s): self.sh_at[str(s)] for s in steps},
                "max": {str(q): [float(x) + 0.0 for x in v]
                        for q, v in self.sh_hi.items()},
                "min": {str(q): [float(x) + 0.0 for x in v]
                        for q, v in self.sh_lo.items()},
            }
        if self.frames:
            steps = sorted(int(s) for s in self.fr_disp)
            extra["frames"] = {
                "steps": steps,
                "t": [float(result.t[s - 1]) for s in steps
                      if 0 < s <= len(result.t)],
                "node_disp": {str(s): self.fr_disp[str(s)] for s in steps}}
        result.extra = extra


def th_begin(eng, asm, th, n_steps: int = 0) -> Optional[THRecorder]:
    """``run_time_history`` hook (after the gravity stage): a recorder,
    or None for a model without cuts and a case without shell output."""
    from skyframe.core import th_output as _tho
    shells = _tho.shell_request(th)
    geo = geometry(eng, asm)
    frames = _tho.frame_steps(th, n_steps)
    if geo is None and shells is None and not frames:
        return None
    return THRecorder(eng, asm, th, geo, shells, frames)


# --------------------------------------------------------------------------- #
# nonlinear static per-step recorder
# --------------------------------------------------------------------------- #
class NLSRecorder:
    def __init__(self, eng, asm, st, geo: Geometry):
        self.eng, self.asm, self.st, self.geo = eng, asm, st, geo
        self.rows: List[np.ndarray] = []

    def record(self, i: int, lam: float) -> None:
        from skyframe.engine.nonlinear_static import _scale_records
        st = self.st
        if i == 0:
            recs, fef = st.recs, st.fef
        else:
            r, f = _scale_records(self.eng._seg_span_loads,
                                  self.eng._seg_fef, lam)
            recs = {k: list(v) for k, v in st.recs.items()}
            for k, lst in r.items():
                recs.setdefault(k, []).extend(lst)
            fef = dict(st.fef)
            for k, v in f.items():
                fef[k] = fef[k] + v if k in fef else v
        self.rows.append(live_values(self.eng, self.asm, self.geo,
                                     recs, fef))

    def results(self) -> dict:
        return self.geo.series(np.vstack(self.rows) if self.rows
                               else np.zeros((0, self.geo.n)))


def nls_begin(eng, asm, st) -> Optional[NLSRecorder]:
    geo = geometry(eng, asm)
    return None if geo is None else NLSRecorder(eng, asm, st, geo)


# --------------------------------------------------------------------------- #
# run() hook: extend the result blocks
# --------------------------------------------------------------------------- #
def _signed_vec(geo: Geometry, cr) -> Optional[np.ndarray]:
    if cr is None:
        return None
    if geo.needs_nodal and not cr.shell_nodal:
        return None
    return geo.values(cr.member_stations, cr.shell_nodal)


def _put(geo, name, hi, lo, section_cuts, piers, spandrels) -> None:
    c, p, s = geo.blocks(hi, lo)
    if c and name not in section_cuts:
        section_cuts[name] = c
    if p and name not in piers:
        piers[name] = p
    if s and name not in spandrels:
        spandrels[name] = s


def extend(eng, asm, cases, rs_cases, th_cases, nls_res, staged, combos,
           section_cuts, piers, spandrels) -> None:
    """Add RS / TH / nonlinear-static / staged / max-min-combo entries to
    the three result blocks in place (existing entries untouched)."""
    geo = geometry(eng, asm)
    if geo is None:
        return
    from skyframe.core.combos_ext import member_kind
    from skyframe.engine import combos_ext as _cx
    model = eng.model
    rs = eng.__dict__.get("_cutx_rs", {})
    signed: Dict[str, np.ndarray] = {}
    pairs: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}

    def signed_of(name):
        if name not in signed:
            kind = member_kind(model, name)
            cr = (cases.get(name) if kind == "static" else
                  staged[name].case if kind == "staged" and name in staged
                  else combos.get(name))
            signed[name] = _signed_vec(geo, cr)
        return signed[name]

    def th_pair(name):
        if name not in pairs:
            blk = (th_cases[name].extra or {}).get("cut_forces") \
                if name in th_cases else None
            if not blk:
                pairs[name] = None
            else:
                mat = geo.from_series(blk)
                if mat.shape[0] == 0:
                    z = np.zeros(geo.n)
                    pairs[name] = (z, z.copy())
                else:
                    pairs[name] = (mat.max(axis=0), mat.min(axis=0))
        return pairs[name]

    def term(name, f, depth=0):
        kind = member_kind(model, name)
        if kind in ("response_spectrum", "rs_directional"):
            r = rs.get(name)
            if r is None:
                return None
            a = abs(f) * r
            return _cx._Term({"v": a}, {"v": -a + 0.0}, ["v"], {})
        if kind == "time_history":
            p = th_pair(name)
            if p is None:
                return None
            return _cx._pair_term({"v": p[0]}, {"v": p[1]}, ["v"], {}, f)
        if kind == "combo" and name in combos \
                and combos[name].minima is not None:
            p = combo_pair(name, depth + 1)
            if p is None:
                return None
            return _cx._pair_term({"v": p[0]}, {"v": p[1]}, ["v"], {}, f)
        v = signed_of(name)
        if v is None:
            return None
        return _cx._Term({"v": f * v}, {"v": f * v}, ["v"], {})

    def combo_pair(name, depth=0):
        if name in pairs:
            return pairs[name]
        pairs[name] = None
        cb = model.combos.get(name)
        if cb is None or depth > 64:
            return None
        ctype = getattr(cb, "combo_type", "add")
        items = (list(_cx.leaf_factors(model, name).items())
                 if ctype == "add" else list(cb.cases.items()))
        terms = [term(c, float(f), depth) for c, f in items]
        if not terms or any(t is None for t in terms):
            return None
        hi, lo, _o, _x = _cx.combine_terms(ctype, terms)
        pairs[name] = (hi["v"], lo["v"])
        return pairs[name]

    for name in rs_cases:                        # RS (+ directional)
        if name in rs:
            _put(geo, name, rs[name], None, section_cuts, piers, spandrels)
    for name in th_cases:                        # TH peaks (max / min)
        p = th_pair(name)
        if p is not None:
            _put(geo, name, p[0], p[1], section_cuts, piers, spandrels)
    for name, res in (nls_res or {}).items():    # NL static final state
        blk = (res.history or {}).get("cut_forces")
        v = None
        if blk:
            mat = geo.from_series(blk)
            if mat.shape[0]:
                v = mat[-1]
        if v is None:
            v = _signed_vec(geo, res.case)
        if v is not None:
            _put(geo, name, v, None, section_cuts, piers, spandrels)
    for name, sres in (staged or {}).items():    # staged final state
        v = _signed_vec(geo, sres.case)
        if v is not None:
            _put(geo, name, v, None, section_cuts, piers, spandrels)
    for name, cr in combos.items():              # max / min combos
        if cr.minima is None:
            continue
        p = combo_pair(name)
        if p is not None:
            _put(geo, name, p[0], p[1], section_cuts, piers, spandrels)
