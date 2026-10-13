"""Step / envelope states of multi-step cases for the graphics (Wave G).

ETABS shows deformed shapes and member force diagrams of time-history,
nonlinear static (pushover) and staged-construction cases at a chosen
step / time, or as the max / min envelope over the steps
(Display > Deformed Shape / Force Diagrams, "Multivalued Options").  The
analysis results JSON keeps only histories of a few quantities (story
displacements, base shears, capacity curves) and the final states; this
module records the FULL state (joint displacements, support reactions and
member station forces) at selected steps -- and, optionally, their max /
min over every step -- when, and only when, an engine carries a request:

    eng._gres_req = {"frames": 40, "members": True, "envelope": False}

Nothing is recorded otherwise (every hook below returns at once), so a
normal ``engine.run()`` and its results JSON are untouched.  The request
is set by :func:`run_steps` (``POST /api/results/steps``) on a FRESH
engine that re-runs the one case: the analysis itself is the unchanged
engine code path, so the recorded states are the states of that run
(identical numbers -- the hooks only read the OpenSees domain).

Recorded quantities per kind (CONTRACT "Step and envelope results for
graphics"):

* time history (direct integration) -- past the gravity state, like every
  TH series: ``node_disp`` (every node, 6 dof), ``reactions`` (supports,
  hinge duplicates folded in, spring reactions added) and member
  ``member_stations`` (the static-case station block, from the element
  forces of the step: :func:`skyframe.engine.cut_results.live_stations`
  minus the gravity-state stations).  ``envelope`` adds the signed max /
  min of every recorded quantity over EVERY step (or every ``stride``-th
  step on very large runs; the stride is reported).
* pushover and nonlinear static -- the TOTAL state at the step (the state
  the structure is in; a nonlinear static case continues its start_from
  chain, so its values are cumulative exactly like its final state).
  Nonlinear static step ``i`` uses the span-load records scaled by the
  step's load factor (the cut-force recorder rule), entry 0 = the initial
  state.
* staged construction -- the accumulated state at the end of every stage
  (per-story stages: story k built and loaded; user stages: the stage
  list) and the final state (include_live / shrinkage increments).

Frames: ``frames`` evenly spaced steps, the last step always included
(the ``th_output.frames`` rule); ``frames >= n`` keeps every step.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import numpy as np
import openseespy.opensees as ops

ST_KEYS = ("N", "V2", "V3", "T", "M2", "M3")
DEFAULT_FRAMES = 40
MAX_FRAMES = 400
ENV_WORK = 5_000_000          # node-dof + station samples before striding
KINDS = ("th", "pushover", "nonlinear_static", "staged")


def request_of(eng) -> Optional[dict]:
    return getattr(eng, "_gres_req", None)


def pick_steps(n: int, frames: int) -> List[int]:
    """1-based recorded steps: ``frames`` evenly spaced, last included."""
    if n <= 0 or frames <= 0:
        return []
    nf = min(int(frames), int(n))
    return sorted({max(1, round(n * (k + 1) / nf)) for k in range(nf)})


def _out(eng) -> dict:
    d = getattr(eng, "_gres_out", None)
    if d is None:
        d = eng._gres_out = {}
    return d


def _f(v) -> float:
    return float(v) + 0.0


def _disp_all(asm) -> Dict[int, List[float]]:
    out = {}
    for t in asm.node_coords:
        u = ops.nodeDisp(t)
        out[t] = [float(u[i]) for i in range(min(6, len(u)))] + \
            [0.0] * max(0, 6 - len(u))
    return out


def _reactions(eng, asm) -> Dict[int, List[float]]:
    from skyframe.engine.nonlinear_static import _reactions as _nls_reac
    return {t: [float(x) for x in v] for t, v in _nls_reac(eng, asm).items()}


def _stations(eng, asm, recs=None, fef=None) -> Dict[str, dict]:
    from skyframe.engine.cut_results import live_stations
    return live_stations(eng, asm, list(eng.model.members), recs, fef)


def _minus(a: Dict, b: Optional[Dict]) -> Dict:
    if b is None:
        return a
    return {t: [x - y for x, y in zip(v, b.get(t, [0.0] * len(v)))]
            for t, v in a.items()}


def _st_minus(a: Dict[str, dict], b: Optional[Dict[str, dict]]):
    if b is None:
        return a
    out = {}
    for uid, s in a.items():
        r = b.get(uid)
        out[uid] = {"x": list(s["x"]), **{
            k: ([x - y for x, y in zip(s[k], r[k])] if r is not None
                else list(s[k])) for k in ST_KEYS}}
    return out


def _clean_map(d: Dict) -> Dict[str, List[float]]:
    return {str(t): [_f(x) for x in v] for t, v in d.items()}


def _clean_st(d: Dict[str, dict]) -> Dict[str, dict]:
    return {uid: {"x": [float(x) for x in s["x"]],
                  **{k: [_f(x) for x in s[k]] for k in ST_KEYS}}
            for uid, s in d.items()}


class _Envelope:
    """Signed running max / min of node maps and station blocks."""

    def __init__(self):
        self.n_hi: Dict = {}
        self.n_lo: Dict = {}
        self.r_hi: Dict = {}
        self.r_lo: Dict = {}
        self.s_hi: Dict = {}
        self.s_lo: Dict = {}
        self.count = 0

    @staticmethod
    def _upd(hi, lo, key, arr):
        a = np.asarray(arr, dtype=float)
        h = hi.get(key)
        if h is None:
            hi[key] = a.copy()
            lo[key] = a.copy()
        else:
            np.maximum(h, a, out=h)
            np.minimum(lo[key], a, out=lo[key])

    def add(self, disp=None, reac=None, st=None):
        self.count += 1
        for t, v in (disp or {}).items():
            self._upd(self.n_hi, self.n_lo, t, v)
        for t, v in (reac or {}).items():
            self._upd(self.r_hi, self.r_lo, t, v)
        for uid, s in (st or {}).items():
            for k in ST_KEYS:
                self._upd(self.s_hi, self.s_lo, (uid, k), s[k])

    def block(self, stations_x: Dict[str, list]) -> dict:
        def nodes(m):
            return {str(t): [_f(x) for x in v] for t, v in m.items()}

        def sts(m):
            out = {}
            for uid, xs in stations_x.items():
                if (uid, "N") not in m:
                    continue
                out[uid] = {"x": [float(x) for x in xs],
                            **{k: [_f(x) for x in m[(uid, k)]]
                               for k in ST_KEYS}}
            return out
        return {"max": {"node_disp": nodes(self.n_hi),
                        "reactions": nodes(self.r_hi),
                        "member_stations": sts(self.s_hi)},
                "min": {"node_disp": nodes(self.n_lo),
                        "reactions": nodes(self.r_lo),
                        "member_stations": sts(self.s_lo)},
                "samples": self.count}


class StepRecorder:
    """Per-step state recorder shared by TH / pushover / nonlinear static.

    ``relative``: subtract the state at :meth:`__init__` (TH: past the
    gravity state).  ``record(step, axis)`` with a 1-based step."""

    def __init__(self, eng, asm, n_steps: int, req: dict, *,
                 relative: bool, axis_name: str):
        self.eng, self.asm = eng, asm
        self.req = req
        self.n = int(n_steps)
        self.steps = set(pick_steps(self.n, int(req.get("frames")
                                                 or DEFAULT_FRAMES)))
        self.members = bool(req.get("members", True))
        self.want_env = bool(req.get("envelope", False))
        self.relative = relative
        self.axis_name = axis_name
        self.base_disp = _disp_all(asm) if relative else None
        self.base_reac = _reactions(eng, asm) if relative else None
        self.base_st = (_stations(eng, asm)
                        if relative and self.members else None)
        n_nodes = len(asm.node_coords)
        n_st = sum(len(s["x"]) for s in (self.base_st or {}).values()) \
            if self.base_st else 11 * len(eng.model.members)
        work = max(1, self.n) * (6 * n_nodes + (6 * n_st if self.members
                                                else 0))
        self.stride = max(1, int(np.ceil(work / ENV_WORK))) \
            if self.want_env else 0
        self.env = _Envelope() if self.want_env else None
        self.disp: Dict[str, dict] = {}
        self.reac: Dict[str, dict] = {}
        self.st: Dict[str, dict] = {}
        self.axis: Dict[int, float] = {}
        self.xs: Dict[str, list] = {}
        self.t0 = time.perf_counter()

    def record(self, step: int, axis: float, recs=None, fef=None) -> None:
        keep = step in self.steps or step == 0 and 0 in self.steps
        env = self.env is not None and step > 0 and (
            step % self.stride == 0 or step == self.n)
        if not (keep or env):
            return
        disp = _minus(_disp_all(self.asm), self.base_disp)
        reac = _minus(_reactions(self.eng, self.asm), self.base_reac)
        st = None
        if self.members:
            st = _st_minus(_stations(self.eng, self.asm, recs, fef),
                           self.base_st)
            if not self.xs:
                self.xs = {u: s["x"] for u, s in st.items()}
        if env:
            self.env.add(disp, reac, st)
        if keep:
            k = str(step)
            self.disp[k] = _clean_map(disp)
            self.reac[k] = _clean_map(reac)
            if st is not None:
                self.st[k] = _clean_st(st)
            self.axis[step] = float(axis)

    def payload(self, kind: str, name: str) -> dict:
        steps = sorted(self.axis)
        out = {"case": name, "kind": kind, "n_steps": self.n,
               "steps": steps,
               "axis": {"name": self.axis_name,
                        "values": [self.axis[s] for s in steps]},
               "node_disp": {str(s): self.disp[str(s)] for s in steps},
               "reactions": {str(s): self.reac[str(s)] for s in steps}}
        if self.members:
            out["member_stations"] = {str(s): self.st.get(str(s), {})
                                      for s in steps}
        if self.env is not None:
            env = self.env.block(self.xs)
            env["stride"] = self.stride
            out["envelope"] = env
        out["record_s"] = round(time.perf_counter() - self.t0, 4)
        return out


# --------------------------------------------------------------------------- #
# engine hooks (each returns None / does nothing without a request)
# --------------------------------------------------------------------------- #
def th_begin(eng, asm, th, n_steps: int) -> Optional[StepRecorder]:
    """``run_time_history`` hook, after the gravity stage."""
    req = request_of(eng)
    if req is None or req.get("case") != th.name:
        return None
    return StepRecorder(eng, asm, n_steps, req, relative=True, axis_name="t")


def th_finish(eng, rec: Optional[StepRecorder], name: str) -> None:
    if rec is not None:
        _out(eng)[name] = rec.payload("th", name)


def po_begin(eng, asm, name: str, n_steps: int) -> Optional[StepRecorder]:
    """``run_pushover`` hook, after the gravity / start state."""
    req = request_of(eng)
    if req is None or req.get("case") != name:
        return None
    return StepRecorder(eng, asm, n_steps, req, relative=False,
                        axis_name="roof_disp")


def po_finish(eng, rec: Optional[StepRecorder], name: str) -> None:
    if rec is not None:
        _out(eng)[name] = rec.payload("pushover", name)


class _NLS(StepRecorder):
    def __init__(self, eng, asm, st, n_steps, req):
        super().__init__(eng, asm, n_steps, req, relative=False,
                         axis_name="load_factor")
        self.chain_state = st
        self.steps.add(0)

    def record_nls(self, i: int, lam: float) -> None:
        from skyframe.engine.nonlinear_static import _scale_records
        st = self.chain_state
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
        self.record(i, lam, recs, fef)


def nls_begin(eng, asm, st, case) -> Optional[_NLS]:
    """``nonlinear_static.run_case`` hook (before the step-0 sample)."""
    req = request_of(eng)
    if req is None or req.get("case") != case.name:
        return None
    return _NLS(eng, asm, st, int(case.steps), req)


def nls_finish(eng, rec, name: str) -> None:
    if rec is not None:
        _out(eng)[name] = rec.payload("nonlinear_static", name)


# ---- staged: per-story loop snapshots (coordinate keyed) ----
def staged_snap(eng, name: str, k: int, acc: dict) -> None:
    """``run_staged`` hook: accumulated state after stage ``k``."""
    req = request_of(eng)
    if req is None or req.get("case") != name:
        return
    snaps = eng.__dict__.setdefault("_gres_staged", [])
    snaps.append({
        "label": eng.model.stories[k].name if k < len(eng.model.stories)
        else f"stage {k + 1}",
        "disp": {key: np.array(v, dtype=float)
                 for key, v in acc["disp"].items()},
        "master": {s: np.array(v, dtype=float)
                   for s, v in acc["master"].items()},
        "reac": {key: np.array(v, dtype=float)
                 for key, v in acc["reac"].items()},
        "st": {uid: {kk: (list(v) if kk == "x" else np.array(v, dtype=float))
                     for kk, v in s.items()}
               for uid, s in acc["st"].items()},
    })


def staged_finish(eng, name: str, full_asm, pkey, acc=None) -> None:
    """``run_staged`` hook after the one-shot solve: tag the snapshots by
    the full model's FE nodes (+ the final state when include_live /
    shrinkage add increments after the last stage)."""
    req = request_of(eng)
    snaps = eng.__dict__.pop("_gres_staged", None)
    if req is None or req.get("case") != name or not snaps:
        return
    sc = eng.model.staged_cases[name]
    if acc is not None and (sc.include_live
                            or getattr(sc, "time_dependent", None)):
        eng.__dict__["_gres_staged"] = snaps
        staged_snap(eng, name, len(eng.model.stories), acc)
        snaps = eng.__dict__.pop("_gres_staged")
        snaps[-1]["label"] = "Final"
    z6 = np.zeros(6)
    steps, labels, nd, rc, ms = [], [], {}, {}, {}
    for i, sn in enumerate(snaps, start=1):
        disp = {t: [_f(v) for v in sn["disp"].get(pkey(c), z6)]
                for t, c in full_asm.struct_coords.items()}
        for s_name, mt in full_asm.masters.items():
            disp[mt] = [_f(v) for v in sn["master"].get(s_name, z6)]
        reac = {t: [_f(v) for v in sn["reac"].get(
            pkey(full_asm.node_coords[t]), z6)]
            for t in full_asm.support_tags}
        steps.append(i)
        labels.append(sn["label"])
        nd[str(i)] = _clean_map(disp)
        rc[str(i)] = _clean_map(reac)
        ms[str(i)] = {uid: {"x": [float(x) for x in s["x"]],
                            **{k: [_f(x) for x in s[k]] for k in ST_KEYS}}
                      for uid, s in sn["st"].items()}
    _out(eng)[name] = {"case": name, "kind": "staged", "n_steps": len(steps),
                       "steps": steps, "labels": labels,
                       "axis": {"name": "stage",
                                "values": [float(s) for s in steps]},
                       "node_disp": nd, "reactions": rc,
                       "member_stations": ms}


# ---- staged: user-defined stage list ----
def user_stage(eng, name: str, label: str, snap) -> None:
    """``staged_user.run_user_staged`` hook: the cumulative CaseResults at
    the end of a stage (its stations are not in the results JSON)."""
    req = request_of(eng)
    if req is None or req.get("case") != name:
        return
    lst = eng.__dict__.setdefault("_gres_user", [])
    lst.append({"label": label,
                "node_disp": _clean_map(snap.node_disp),
                "reactions": _clean_map(snap.reactions),
                "member_stations": _clean_st(snap.member_stations)})


def _user_payload(eng, name: str, res=None) -> Optional[dict]:
    lst = eng.__dict__.pop("_gres_user", None)
    if not lst:
        return None
    sc = eng.model.staged_cases[name]
    if res is not None and (sc.include_live
                            or getattr(sc, "time_dependent", None)):
        fin = res.case
        lst.append({"label": "Final",
                    "node_disp": _clean_map(fin.node_disp),
                    "reactions": _clean_map(fin.reactions),
                    "member_stations": _clean_st(fin.member_stations)})
    steps = list(range(1, len(lst) + 1))
    return {"case": name, "kind": "staged", "n_steps": len(steps),
            "steps": steps, "labels": [e["label"] for e in lst],
            "axis": {"name": "stage", "values": [float(s) for s in steps]},
            "node_disp": {str(i): e["node_disp"] for i, e in zip(steps, lst)},
            "reactions": {str(i): e["reactions"] for i, e in zip(steps, lst)},
            "member_stations": {str(i): e["member_stations"]
                                for i, e in zip(steps, lst)}}


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def case_kind(model, name: str) -> Optional[str]:
    if name in (getattr(model, "th_cases", None) or {}):
        return "th"
    if name in (getattr(model, "pushover_cases", None) or {}):
        return "pushover"
    if name in (getattr(model, "nonlinear_static_cases", None) or {}):
        return "nonlinear_static"
    if name in (getattr(model, "staged_cases", None) or {}):
        return "staged"
    return None


def normalize_request(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    case = body.get("case")
    if not isinstance(case, str) or not case:
        raise ValueError("'case' (a time-history, pushover, nonlinear "
                         "static or staged case name) is required")
    fr = body.get("frames", DEFAULT_FRAMES)
    if isinstance(fr, bool) or not isinstance(fr, (int, float)) \
            or float(fr) != int(fr) or not 1 <= int(fr) <= MAX_FRAMES:
        raise ValueError(f"'frames' must be an integer in [1, {MAX_FRAMES}]")
    for k in ("members", "envelope"):
        if k in body and not isinstance(body[k], bool):
            raise ValueError(f"'{k}' must be true or false")
    return {"case": case, "frames": int(fr),
            "members": bool(body.get("members", True)),
            "envelope": bool(body.get("envelope", False))}


def run_steps(model, req: dict) -> dict:
    """Re-run one multi-step case on a fresh engine with the recorder on;
    returns the step payload (see module docstring)."""
    from skyframe.engine.opensees_engine import OpenSeesEngine
    name = req["case"]
    kind = case_kind(model, name)
    if kind is None:
        raise ValueError(f"{name!r} is not a time-history, pushover, "
                         "nonlinear static or staged case of this model")
    t0 = time.perf_counter()
    eng = OpenSeesEngine(model)
    eng._gres_req = dict(req)
    if kind == "th":
        th = model.th_cases[name]
        if (getattr(th, "th_type", None) or "direct") != "direct":
            raise ValueError(f"{name!r}: modal / FNA time histories record "
                             "no step states (direct integration only)")
        res = eng.run_time_history(name)
        pay = _out(eng).get(name)
        if pay is None:
            raise ValueError(f"{name!r}: no step states were recorded "
                             "(this time history runs by a modal / named "
                             "modal-case method, not direct integration)")
        pay["axis"]["values"] = [float(res.t[s - 1]) if 0 < s <= len(res.t)
                                 else 0.0 for s in pay["steps"]]
    elif kind == "pushover":
        res = eng.run_pushover(name)
        pay = _out(eng).get(name)
        if pay is None:
            raise ValueError(f"{name!r}: no step states were recorded")
        rd = list(res.roof_disp)
        pay["axis"]["values"] = [float(rd[s - 1]) if 0 < s <= len(rd)
                                 else 0.0 for s in pay["steps"]]
        pay["base_shear"] = [float(res.base_shear[s - 1])
                             if 0 < s <= len(res.base_shear) else 0.0
                             for s in pay["steps"]]
        pay["n_steps"] = len(rd)
    elif kind == "nonlinear_static":
        from skyframe.engine import nonlinear_static as _nls
        _nls.run_case(eng, name)
        pay = _out(eng).get(name)
        if pay is None:
            raise ValueError(f"{name!r}: no step states were recorded")
    else:
        res = eng.run_staged(name)
        pay = _out(eng).get(name) or _user_payload(eng, name, res)
        if pay is None:
            raise ValueError(f"{name!r}: no stage states were recorded")
    if "member_stations" in pay:
        pay["member_peaks"] = {k: _peaks(b) for k, b in pay["member_stations"].items()}
    pay["elapsed_s"] = round(time.perf_counter() - t0, 4)
    pay["frames"] = req["frames"]
    return pay


def _peaks(block: Dict[str, dict]) -> Dict[str, float]:
    """Largest |value| per station component over the members of a step
    (fixed diagram scale when the per-step blocks are fetched lazily)."""
    out = {k: 0.0 for k in ST_KEYS}
    for s in block.values():
        for k in ST_KEYS:
            for v in s.get(k, ()):
                a = abs(v)
                if a > out[k]:
                    out[k] = a
    return out
