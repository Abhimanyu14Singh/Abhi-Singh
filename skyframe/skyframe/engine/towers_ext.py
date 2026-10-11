"""Multiple towers — engine follow-ups (CONTRACT "Multiple towers —
follow-ups").

Every hook here is inert without ``model.towers`` (and without the new
optional fields), so legacy results stay byte-identical.

Time history / FNA story shear per tower
----------------------------------------
With towers, the story shear history of a direct-integration time
history is the SAME horizontal free-body element cut used for static
cases (:func:`skyframe.engine.towers.tower_cut_shears`), evaluated on the
converged state of every step: the tower's own frame segments, shell
quads and links crossing the plane just below the level, minus the
reactions of its supports above the plane.  The series is the increment
past the held gravity state (sample k = t = (k+1) dt, like every TH
series).  Element forces are the RESTORING forces (OpenSees element
resisting forces): Rayleigh / modal viscous damping forces are not
element forces and are not part of the cut (ETABS story forces from
element forces); the legacy inertia rule includes them.  For an undamped
run the two coincide to solver precision.

FNA: the linear structure's element forces are linear in the modal
coordinates, ``V(t) = sum_i q_i(t) V_i`` with ``V_i`` the per-tower cut
shear of the static solve ``K u = w_i^2 M phi_i`` (whose solution is
``phi_i``; the same per-mode solve response spectrum uses), plus the
horizontal force of every nonlinear device link crossing the plane
(``F_dev - k_lin d``, the part not in the linear basis).

``peaks.story[s].shear_x / shear_y`` become the per-tower cut peaks and
the result gains ``towers`` ({tower: {stories, base_shear: {FX, FY}}} —
peak |V| of the tower's lowest story) and ``story_shear_method``.
``TimeHistoryCase.output_requests.story_shears = true`` adds the full
history ``story_shear_history`` (also without towers: then the legacy
inertia series).

Pushover / nonlinear static control tower
-----------------------------------------
``control_tower``: the monitored joint is the tower's top-story master
(else its lowest-tag node at that level); for the default ``roof_point``
pushover distribution the unit push acts there too; H_ctrl = that
story's elevation.

Staged construction across towers
---------------------------------
``per_story`` stages follow :func:`skyframe.core.towers_ext.stage_order`
(ascending elevation, ties -> tower order); members / shells / links /
loads go to the story of their OWN tower owning their top elevation;
every stage sub-model carries the towers, so each (tower, story) keeps its
own diaphragm.
"""
from __future__ import annotations

import copy
import functools
from typing import Dict, List, Optional, Sequence

import numpy as np

from skyframe.core import towers as _ct
from skyframe.core import towers_ext as _cx
from skyframe.engine import towers as _tw

_TOL = 1e-6


def _peak(vals: Sequence[float]) -> float:
    return max(abs(v) for v in vals) if len(vals) else 0.0


def _requested(th) -> bool:
    req = getattr(th, "output_requests", None)
    return bool(isinstance(req, dict) and req.get("story_shears"))


def _tower_block(eng, peaks_story) -> dict:
    out = {}
    for tw, ent in _ct.summary(eng.model).items():
        first = ent["stories"][0] if ent["stories"] else None
        row = peaks_story.get(first, {}) if first else {}
        out[tw] = {"stories": list(ent["stories"]),
                   "base_shear": {"FX": float(row.get("shear_x", 0.0)),
                                  "FY": float(row.get("shear_y", 0.0))}}
    return out


def _finish(eng, result, vx: Dict[str, List[float]],
            vy: Dict[str, List[float]], method: str, requested: bool
            ) -> None:
    ps = result.peaks.get("story") or {}
    if method == "element_cut":
        for s in eng.model.stories:
            if s.name in ps:
                ps[s.name]["shear_x"] = _peak(vx[s.name])
                ps[s.name]["shear_y"] = _peak(vy[s.name])
        result.extra = dict(result.extra, story_shear_method=method,
                            towers=_tower_block(eng, ps))
    if requested:
        hist = {"method": method,
                "VX": {s.name: [float(v) for v in vx[s.name]]
                       for s in eng.model.stories},
                "VY": {s.name: [float(v) for v in vy[s.name]]
                       for s in eng.model.stories}}
        if _ct.has_towers(eng.model):
            hist["story_tower"] = _ct.story_tower_map(eng.model)
        result.extra = dict(result.extra, story_shear_history=hist)


# ------------------------------------------------- direct integration TH
class THCut:
    """Per-step per-tower cut-shear recorder of one direct-integration
    run (see module docstring)."""

    def __init__(self, eng, asm, th, active: bool, requested: bool):
        self.eng, self.asm = eng, asm
        self.active, self.requested = active, requested
        names = [s.name for s in eng.model.stories]
        self.vx: Dict[str, List[float]] = {n: [] for n in names}
        self.vy: Dict[str, List[float]] = {n: [] for n in names}
        self.base = {n: (0.0, 0.0) for n in names}
        if active and th.gravity:
            self.base = self._cut()

    def _cut(self):
        eng = self.eng
        saved = eng._seg_span_loads, eng._seg_fef
        eng._seg_span_loads, eng._seg_fef = {}, {}
        try:
            return _tw.tower_cut_shears(eng, self.asm)
        finally:
            eng._seg_span_loads, eng._seg_fef = saved

    def record(self) -> None:
        if not self.active:
            return
        c = self._cut()
        for n, (b0, b1) in self.base.items():
            v = c.get(n, (0.0, 0.0))
            self.vx[n].append(v[0] - b0)
            self.vy[n].append(v[1] - b1)

    def finish(self, result, svx, svy) -> None:
        if self.active:
            _finish(self.eng, result, self.vx, self.vy, "element_cut",
                    self.requested)
        else:
            _finish(self.eng, result, svx, svy, "inertia", self.requested)


def th_begin(eng, asm, th) -> Optional[THCut]:
    """A recorder when the model has towers or the case requests the
    story-shear history; ``None`` otherwise (legacy path untouched)."""
    active = _tw.active(eng)
    req = _requested(th)
    if not (active or req):
        return None
    return THCut(eng, asm, th, active, req)


# ------------------------------------------------------------------- FNA
def _device_axes(model):
    """[(link, horizontal axis (2,), sign of the upper end)] per FNA
    device component, in run_fna's component order."""
    out = []
    for lk in model.links:
        lt = getattr(lk, "link_type", "elastic")
        if lt == "elastic":
            continue
        pi, pj = np.asarray(lk.pi, float), np.asarray(lk.pj, float)
        up = 1.0 if pj[2] >= pi[2] else -1.0
        if lt in ("damper", "gap", "hook"):
            e = pj - pi
            e = e / float(np.linalg.norm(e))
            out.append((lk, e[:2], up))
        else:                                       # isolator: x, y
            out.append((lk, np.array([1.0, 0.0]), up))
            out.append((lk, np.array([0.0, 1.0]), up))
    return out


def fna(eng, th, result, svx, svy, eng2, shp, w, mass_items, q, fdev, B,
        k_lin) -> None:
    """Per-tower FNA story shear (see module docstring); also the
    requested history without towers (legacy inertia series)."""
    active = _tw.active(eng)
    req = _requested(th)
    if not (active or req):
        return
    if not active:
        _finish(eng, result, svx, svy, "inertia", req)
        return
    model = eng.model
    names = [s.name for s in model.stories]
    nm = len(w)
    rows_x = {n: np.zeros(nm) for n in names}
    rows_y = {n: np.zeros(nm) for n in names}
    for i in range(nm):
        w2 = float(w[i]) ** 2
        loads = {}
        for (t, d), mv in mass_items:
            v = w2 * mv * float(shp[i][t][d - 1])
            if v != 0.0:
                loads[(t, d)] = loads.get((t, d), 0.0) + v
        st = eng2._modal_static(loads).story
        for n in names:
            row = st.get(n)
            if row is not None:
                rows_x[n][i] = row["shear_x"]
                rows_y[n][i] = row["shear_y"]
    qa = np.asarray(q, float)
    vx = {n: qa @ rows_x[n] for n in names}
    vy = {n: qa @ rows_y[n] for n in names}
    # nonlinear device links crossing a cut plane of their own tower
    axes = _device_axes(model)
    if axes and fdev is not None and len(axes) == np.asarray(fdev).shape[1]:
        ctx = eng.__dict__["_tw"]["ctx"]
        st_map = eng.__dict__["_tw"]["story_tower"]
        F = np.asarray(fdev, float)
        Bm = np.asarray(B, float)
        kl = np.asarray(k_lin, float)
        for c, (lk, ax, up) in enumerate(axes):
            tw = ctx.obj(lk)
            zlo = min(lk.pi[2], lk.pj[2])
            zhi = max(lk.pi[2], lk.pj[2])
            g = F[:, c] - kl[c] * (qa @ Bm[c])
            for s in model.stories:
                zc = s.elevation - _tw._CUT_DELTA
                if st_map[s.name] != tw or not (zlo < zc < zhi):
                    continue
                vx[s.name] = vx[s.name] + up * ax[0] * g
                vy[s.name] = vy[s.name] + up * ax[1] * g
    _finish(eng, result, {n: [float(v) for v in vx[n]] for n in names},
            {n: [float(v) for v in vy[n]] for n in names}, "element_cut",
            req)


# --------------------------------------------- pushover / NLS control
def _roof_node(asm, story, label: str) -> int:
    if story.name in asm.masters:
        return asm.masters[story.name]
    nodes = asm.story_nodes.get(story.name) or []
    if not nodes:
        raise ValueError(f"{label}: control tower story {story.name!r} has "
                         "no nodes")
    return min(nodes)


def pushover_roof(eng, asm, case, top, ctrl, H):
    """(top story, control node, H) of a pushover; unchanged unless the
    case sets ``control_tower``."""
    tw = getattr(case, "control_tower", None)
    if not tw:
        return top, ctrl, H
    s = _cx.control_story(eng.model, tw)
    return s, _roof_node(asm, s, f"Pushover case {case.name!r}"), \
        s.elevation


def nls_monitor(eng, asm, case) -> Optional[int]:
    """Monitored node of a nonlinear static case with ``control_tower``
    (``None`` = not set)."""
    tw = getattr(case, "control_tower", None)
    if not tw:
        return None
    s = _cx.control_story(eng.model, tw)
    return _roof_node(asm, s, f"Nonlinear static case {case.name!r}")


# ------------------------------------------------- staged construction
def install(eng) -> None:
    """Bind the staged-construction override (towers only)."""
    cls = type(eng)
    eng.__dict__["run_staged"] = functools.partial(_run_staged, eng, cls)


def _run_staged(eng, cls, name):
    model = eng.model
    sc = model.staged_cases.get(name)
    if (sc is None or isinstance(sc.stages, list)
            or name in eng._staged_cache or not model.stories):
        return cls.run_staged(eng, name)
    order = _cx.stage_order(model)
    by = {s.name: s for s in model.stories}
    view = copy.copy(model)
    view.stories = [by[n] for n in order]
    ctx = _ct.context(model)
    d = eng.__dict__
    keys = ("_stage_of_member", "_stage_of_region", "_split_staged_pattern",
            "_stage_submodel")

    def stage_of_obj(obj, sidx, z):
        st = getattr(obj, "story", "") or ""
        if st in sidx:
            return sidx[st]
        s = _cx.tower_story_of_z(model, ctx.obj(obj), z)
        return sidx[s.name]

    def stage_of_member(m, sidx):
        return stage_of_obj(m, sidx, max(m.pi[2], m.pj[2]))

    def stage_of_region(r, sidx):
        return stage_of_obj(r, sidx, max(c[2] for c in r.corners))

    def split(pat):
        parts = cls._split_staged_pattern(eng, pat)
        sidx = {n: i for i, n in enumerate(order)}
        for p in parts:
            p.nodal_loads = []
        for nl in pat.nodal_loads:
            s = _cx.tower_story_of_z(model, ctx.point(nl.point),
                                     nl.point[2])
            parts[sidx[s.name]].nodal_loads.append(nl)
        return parts

    def submodel(k):
        sub = cls._stage_submodel(eng, k)
        sidx = {n: i for i, n in enumerate(order)}
        sub.links = [lk for lk in model.links
                     if stage_of_obj(lk, sidx, max(lk.pi[2], lk.pj[2]))
                     <= k]
        pts = set()
        for m in sub.members:
            pts.add(_ct._key(m.pi))
            pts.add(_ct._key(m.pj))
        for r in sub.shells:
            pts.update(_ct._key(c) for c in r.corners)
        for lk in sub.links:
            pts.add(_ct._key(lk.pi))
            pts.add(_ct._key(lk.pj))
        sub.supports = [s for s in sub.supports if _ct._key(s.point) in pts]
        sub.spring_supports = [s for s in sub.spring_supports
                               if _ct._key(s.point) in pts]
        sub.joint_panel_zones = [e for e in sub.joint_panel_zones
                                 if _ct._key(e["point"]) in pts]
        _cx.carry(model, sub)
        return sub

    d["_stage_of_member"] = stage_of_member
    d["_stage_of_region"] = stage_of_region
    d["_split_staged_pattern"] = split
    d["_stage_submodel"] = submodel
    eng.model = view
    try:
        res = cls.run_staged(eng, name)
    finally:
        eng.model = model
        for k in keys:
            d.pop(k, None)
    for cr in (res.case, res.oneshot):
        if cr is not None and isinstance(cr.story, dict):
            cr.story = {s.name: cr.story[s.name] for s in model.stories
                        if s.name in cr.story}
    return res


# ------------------------------------------------- named diaphragms
def tower_coords(eng, asm, story) -> Optional[dict]:
    """{tag: xyz} of the structural nodes of ``story``'s tower (``None``
    without towers) — named-diaphragm partition per tower."""
    if not _tw.active(eng):
        return None
    nt = _tw.node_towers(eng, asm)
    tw = eng.__dict__["_tw"]["story_tower"].get(story.name)
    return {t: c for t, c in asm.struct_coords.items() if nt.get(t) == tw}
