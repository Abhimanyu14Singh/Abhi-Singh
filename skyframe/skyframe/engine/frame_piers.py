"""Pier forces from FRAME members (column-as-pier) -- CONTRACT "Frame pier
labels".  Pure post-processing of the member station forces (no extra
solve), merged into the wall-pier block ``results["piers"][case][label]
[story] = {"P", "V", "M"}`` with the SAME conventions (CONTRACT v0.15):

* every pier-labelled frame that crosses the story-bottom elevation
  ``z_bot`` of story ``s`` is cut there; the force the part BELOW applies
  to the part ABOVE (``F``, ``Mv``, global) is read from the member's
  station vector at the cut (``_interp_station``; the station vector is the
  force of the j-side part on the i-side part, flipped when j is the upper
  end) -- exact at the member ends;
* ``P = +sum F_z`` (compression positive), ``V = -sum F . hhat``;
* ``M = sum (s - sbar) F_z + Mv . (hhat x ez)`` about the pier reference
  point at ``z_bot``: ``hhat``/``sbar`` of the label's first WALL cut on
  that story when the pier also has walls (boundary columns then add their
  axial couple about the wall centroid), otherwise the frame-only pier
  axis -- the plan direction from the first to the farthest column
  (``hhat`` pointing toward +X, or +Y for a pier along Y) and ``sbar`` the
  area-weighted column centroid along it.  A single-column pier uses the
  column's local-2 axis projected on plan (``V = V2``, ``M = M3``
  magnitudes for an untilted column).

Frame-only labels appear in ``results["piers"]`` only when the model has
pier-labelled frames, so legacy results are unchanged.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from skyframe.core import frame_piers as _fpr

_TOL = 1e-6
_EZ = np.array([0.0, 0.0, 1.0])


def enabled(model) -> bool:
    return _fpr.enabled(model)


def _orient(h: np.ndarray) -> np.ndarray:
    """Point a plan axis toward +X (or +Y when it runs along Y)."""
    if abs(h[0]) > 1e-9:
        return h if h[0] > 0 else -h
    return h if h[1] > 0 else -h


def _frame_cuts(eng, model) -> List[dict]:
    """[{label, story, member, x_cut, z_bot, upper_is_j, p (plan pt)}]."""
    from skyframe.engine.opensees_engine import _local_axes
    out: List[dict] = []
    for m in model.members:
        lab = getattr(m, "pier", "") or ""
        if not lab:
            continue
        zi, zj = float(m.pi[2]), float(m.pj[2])
        if abs(zj - zi) < 1e-9:
            continue
        lo, hi = min(zi, zj), max(zi, zj)
        for s in model.stories:
            z_bot = s.elevation - s.height
            if not (lo <= z_bot + _TOL and hi > z_bot + _TOL):
                continue
            t = (z_bot - zi) / (zj - zi)
            t = min(max(t, 0.0), 1.0)
            p = np.asarray(m.pi, float) + t * (np.asarray(m.pj, float)
                                               - np.asarray(m.pi, float))
            out.append({"label": lab, "story": s.name, "member": m,
                        "x_cut": t * m.length, "z_bot": z_bot,
                        "upper_is_j": zj > zi, "p": p,
                        "axes": _local_axes(m)})
    return out


def _frame_axis(cuts: List[dict], model) -> (np.ndarray, float):
    """(hhat, sbar) of a frame-only pier on one story."""
    pts = [c["p"] for c in cuts]
    if len(pts) > 1:
        far = max(pts[1:], key=lambda q: float(np.hypot(q[0] - pts[0][0],
                                                        q[1] - pts[0][1])))
        d = np.array([far[0] - pts[0][0], far[1] - pts[0][1], 0.0])
        if float(np.linalg.norm(d)) > 1e-9:
            h = _orient(d / np.linalg.norm(d))
        else:
            h = None
    else:
        h = None
    if h is None:
        y2 = np.asarray(cuts[0]["axes"][1], float)      # local 2
        d = np.array([y2[0], y2[1], 0.0])
        if float(np.linalg.norm(d)) < 1e-9:              # pragma: no cover
            d = np.array([1.0, 0.0, 0.0])
        h = d / np.linalg.norm(d)
        if len(cuts) > 1:
            h = _orient(h)
    w = []
    for c in cuts:
        sec = model.sections.get(c["member"].section)
        w.append(float(getattr(sec, "A", 1.0) or 1.0))
    sv = [float(c["p"] @ h) for c in cuts]
    sbar = sum(a * b for a, b in zip(w, sv)) / sum(w)
    return h, sbar


def _cut_force(cut: dict, cr) -> Optional[tuple]:
    from skyframe.engine.opensees_engine import _interp_station
    st = cr.member_stations.get(cut["member"].uid)
    if not st or not st.get("x"):
        return None
    vals = [_interp_station(st, cut["x_cut"], k)
            for k in ("N", "V2", "V3", "T", "M2", "M3")]
    xa, ya, za = (np.asarray(v, float) for v in cut["axes"][:3])
    Fst = vals[0] * xa + vals[1] * ya + vals[2] * za
    Mst = vals[3] * xa + vals[4] * ya + vals[5] * za
    # station = force of the j-side on the i-side; the free body is the
    # UPPER part: i-side when j is lower, j-side (flip) when j is upper
    sgn = -1.0 if cut["upper_is_j"] else 1.0
    return sgn * Fst, sgn * Mst


def add(eng, asm, sources: Dict[str, object], piers: dict) -> dict:
    """Merge frame pier contributions into ``piers`` (returned; the input
    dict is updated in place).  ``sources`` = static cases + additive
    combos (CaseResults)."""
    model = eng.model
    if not enabled(model):
        return piers
    cuts = _frame_cuts(eng, model)
    if not cuts:
        return piers
    wall_ref: Dict[tuple, tuple] = {}
    try:
        for wc in eng._pier_cuts(asm):
            wall_ref.setdefault((wc["label"], wc["story"]),
                                (np.asarray(wc["hhat"], float),
                                 float(wc["sbar"])))
    except Exception:                                   # pragma: no cover
        wall_ref = {}
    groups: Dict[tuple, List[dict]] = {}
    for c in cuts:
        groups.setdefault((c["label"], c["story"]), []).append(c)
    axes: Dict[tuple, tuple] = {}
    for key, lst in groups.items():
        axes[key] = wall_ref.get(key) or _frame_axis(lst, model)
    for cname, cr in sources.items():
        by_pier = piers.get(cname)
        fresh = by_pier is None
        if fresh:
            by_pier = {}
        for key, lst in groups.items():
            hhat, sbar = axes[key]
            n_ip = np.cross(hhat, _EZ)
            P = V = M = 0.0
            got = False
            for c in lst:
                fm = _cut_force(c, cr)
                if fm is None:
                    continue
                got = True
                F, Mv = fm
                P += float(F[2])
                V -= float(F @ hhat)
                M += (float(c["p"] @ hhat) - sbar) * float(F[2])
                M += float(Mv @ n_ip)
            if not got:
                continue
            entry = by_pier.setdefault(key[0], {}).setdefault(
                key[1], {"P": 0.0, "V": 0.0, "M": 0.0})
            entry["P"] += P
            entry["V"] += V
            entry["M"] += M
        if by_pier and fresh:
            piers[cname] = by_pier
    return piers


def frame_pier_axes(eng, asm) -> Dict[str, Dict[str, dict]]:
    """label -> story -> {"hhat": [x, y, z], "sbar", "frames": [uids]}
    (diagnostics / UI)."""
    model = eng.model
    out: Dict[str, Dict[str, dict]] = {}
    groups: Dict[tuple, List[dict]] = {}
    for c in _frame_cuts(eng, model):
        groups.setdefault((c["label"], c["story"]), []).append(c)
    for (lab, st), lst in groups.items():
        h, sbar = _frame_axis(lst, model)
        out.setdefault(lab, {})[st] = {
            "hhat": [float(v) for v in h], "sbar": float(sbar),
            "frames": [c["member"].uid for c in lst]}
    return out

