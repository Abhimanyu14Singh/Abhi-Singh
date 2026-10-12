"""Spandrel forces (ETABS "Spandrel Forces"): vertical free-body cuts at the
two ends of every labeled spandrel, per story and per static case /
additive combo — pure post-processing (no extra solve).

A spandrel is the set of WALL shells (meshed, ``behavior == "shell"``) and
FRAME members carrying the same ``spandrel`` label on one story.  Its local
axes are ETABS-like:

* ``e1`` — horizontal, in the spandrel plane (along the span).  Taken from
  the wall plane (``ez x n``, as for piers) or, for a frame-only spandrel,
  from the first member's plan direction; then oriented so that it points
  toward +X (or +Y when the spandrel runs along Y).  **Left** is the end at
  the smaller ``e1`` coordinate, **Right** the larger.
* ``e2`` — global +Z;  ``e3 = e1 x e2`` (the out-of-plane normal).

Kernels reused from the existing post-processing:

* shells — the exact pier kernel: the 24-component global nodal resisting
  forces of every shell element (``CaseResults.shell_nodal``, captured at
  solve time) summed at the cut-line nodes over the spandrel's OWN elements
  give the force/moment that the rest of the structure applies to the
  spandrel across that cut (exact free body);
* frames — the section-cut kernel: the member's station forces linearly
  interpolated at the cut (``_interp_station``) and rotated local -> global
  with the engine's own ``_local_axes`` (exact for the statics stations).

Reported values are the INTERNAL force at each cut acting on the material
on its LEFT (negative ``e1``) side, from the material on its right — the
ETABS frame convention, so for a frame-only spandrel ``P, V2, M3`` equal the
member's own station ``N, V2, M3`` at the cut.  ``P`` is tension-positive.
Moments are taken about the cut centroid (tributary-height weighted for
shell cut nodes, the crossing points for frames).

``results["spandrels"][case][label][story] = {"Left": {P, V2, V3, T, M2,
M3}, "Right": {...}, "length": L, "n_shells", "n_frames"}`` — emitted only
when the model has spandrel labels.
"""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np

from skyframe.core import spandrels as _spd

_TOL = 1e-6
_KEYS = ("P", "V2", "V3", "T", "M2", "M3")


def enabled(model) -> bool:
    return _spd.enabled(model)


def _story_of(model, obj, zc: float) -> str:
    names = {s.name for s in model.stories}
    st = getattr(obj, "story", "") or ""
    if st in names:
        return st
    for s in model.stories:
        if s.elevation - s.height - _TOL < zc <= s.elevation + _TOL:
            return s.name
    return model.stories[-1].name if model.stories else ""


def _canon(e1: np.ndarray) -> np.ndarray:
    e1 = e1 / np.linalg.norm(e1)
    if e1[0] < -1e-9 or (abs(e1[0]) <= 1e-9 and e1[1] < 0):
        e1 = -e1
    return e1


def _wall_axis(region) -> Optional[np.ndarray]:
    from skyframe.core.polymesh import newell_normal
    c = [np.asarray(p, float) for p in region.corners]
    if len(c) == 4:
        n = np.cross(c[1] - c[0], c[3] - c[0])
    else:
        n = np.asarray(newell_normal(region.corners), float)
    nl = float(np.linalg.norm(n))
    if nl < 1e-12 or abs(n[2]) / nl > 1e-3:
        return None                                  # not a vertical wall
    h = np.cross(np.array([0.0, 0.0, 1.0]), n / nl)
    return _canon(h)


def _frame_axis(m) -> Optional[np.ndarray]:
    d = np.asarray(m.pj, float) - np.asarray(m.pi, float)
    d[2] = 0.0
    if float(np.linalg.norm(d)) < 1e-9:
        return None                                  # vertical member
    return _canon(d)


def spandrel_groups(engine, asm) -> List[dict]:
    """Case-independent geometry of every (label, story) spandrel."""
    model = engine.model
    groups: Dict[Tuple[str, str], dict] = {}
    for r in model.shells:
        lab = getattr(r, "spandrel", "") or ""
        if not lab or r.kind != "wall":
            continue
        if r.behavior != "shell":
            warnings.warn(f"Wall {r.uid!r} (spandrel {lab!r}): spandrel "
                          "forces need meshed shell behavior; skipped")
            continue
        quads = [(qi, [np.asarray(asm.node_coords[t], float)
                       for t in q["nodes"]])
                 for qi, q in enumerate(asm.shell_quads)
                 if q["region"] == r.uid]
        if not quads:
            warnings.warn(f"Wall {r.uid!r} (spandrel {lab!r}): no meshed "
                          "elements; skipped")
            continue
        e1 = _wall_axis(r)
        if e1 is None:
            warnings.warn(f"Wall {r.uid!r} (spandrel {lab!r}): not a "
                          "vertical wall; skipped")
            continue
        zc = float(np.mean([p[2] for _, pts in quads for p in pts]))
        g = groups.setdefault((lab, _story_of(model, r, zc)),
                               {"shells": [], "frames": [], "e1": e1})
        g["shells"].append((r.uid, quads))
    for m in model.members:
        lab = getattr(m, "spandrel", "") or ""
        if not lab:
            continue
        e1 = _frame_axis(m)
        if e1 is None:
            warnings.warn(f"Frame {m.uid!r} (spandrel {lab!r}): vertical "
                          "members cannot be spandrels; skipped")
            continue
        zc = 0.5 * (m.pi[2] + m.pj[2])
        g = groups.setdefault((lab, _story_of(model, m, zc)),
                              {"shells": [], "frames": [], "e1": e1})
        g["frames"].append(m)
    out = []
    ez = np.array([0.0, 0.0, 1.0])
    for (lab, story), g in groups.items():
        e1 = g["e1"]
        e3 = np.cross(e1, ez)
        svals = [float(p @ e1) for _u, quads in g["shells"]
                 for _qi, pts in quads for p in pts]
        for m in g["frames"]:
            svals += [float(np.asarray(m.pi) @ e1), float(np.asarray(m.pj) @ e1)]
        s_lo, s_hi = min(svals), max(svals)
        if s_hi - s_lo < _TOL:
            continue
        cuts = {}
        for side, sc in (("Left", s_lo), ("Right", s_hi)):
            cuts[side] = _cut_geometry(g, sc, e1, e3)
        out.append({"label": lab, "story": story, "e1": e1, "e2": ez,
                    "e3": e3, "length": s_hi - s_lo, "cuts": cuts,
                    "n_shells": len(g["shells"]),
                    "n_frames": len(g["frames"])})
    out.sort(key=lambda g: (g["label"], g["story"]))
    return out


def _cut_geometry(g: dict, sc: float, e1, e3) -> dict:
    """Cut nodes of the spandrel's own elements + frame crossings at
    ``e1 . p == sc`` and the cut centroid."""
    elems = []
    pts_z: List[float] = []
    pts_n: List[float] = []
    for _uid, quads in g["shells"]:
        for qi, pts in quads:
            ks = [k for k, p in enumerate(pts) if abs(float(p @ e1) - sc) < _TOL]
            if ks:
                elems.append((qi, ks, pts))
                for k in ks:
                    pts_z.append(float(pts[k][2]))
                    pts_n.append(float(pts[k] @ e3))
    frames = []
    for m in g["frames"]:
        pi, pj = np.asarray(m.pi, float), np.asarray(m.pj, float)
        si, sj = float(pi @ e1), float(pj @ e1)
        lo, hi = min(si, sj), max(si, sj)
        if hi - lo < _TOL or not (lo - _TOL <= sc <= hi + _TOL):
            continue
        frac = min(max((sc - si) / (sj - si), 0.0), 1.0)
        r = pi + frac * (pj - pi)
        frames.append((m, frac, r, 1.0 if sj > si else -1.0))
    # centroid: tributary-height-weighted shell cut nodes, else frame points
    zs = sorted(set(round(z, 9) for z in pts_z))
    if len(zs) > 1:
        trib = [(zs[min(i + 1, len(zs) - 1)] - zs[max(i - 1, 0)]) / 2.0
                for i in range(len(zs))]
        zbar = sum(z * t for z, t in zip(zs, trib)) / sum(trib)
        nbar = float(np.mean(pts_n))
    elif zs:
        zbar, nbar = zs[0], float(np.mean(pts_n))
    elif frames:
        zbar = float(np.mean([f[2][2] for f in frames]))
        nbar = float(np.mean([float(f[2] @ e3) for f in frames]))
    else:
        zbar = nbar = 0.0
    c = sc * e1 + nbar * e3 + zbar * np.array([0.0, 0.0, 1.0])
    # e1 and e3 are horizontal and orthogonal: c's plan coords are exact
    return {"s": sc, "elems": elems, "frames": frames, "c": c}


def _cut_resultant(cut: dict, cr) -> Tuple[np.ndarray, np.ndarray]:
    """(R, MR): force / moment (about the cut centroid) that the rest of
    the structure applies to the spandrel free body across the cut."""
    from skyframe.engine.opensees_engine import _interp_station, _local_axes
    R = np.zeros(3)
    MR = np.zeros(3)
    c = cut["c"]
    for qi, ks, pts in cut["elems"]:
        f = cr.shell_nodal.get(qi)
        if f is None:
            continue
        for k in ks:
            F = np.asarray(f[6 * k: 6 * k + 3], float)
            Mv = np.asarray(f[6 * k + 3: 6 * k + 6], float)
            R += F
            MR += np.cross(pts[k] - c, F) + Mv
    for m, frac, r, sgn in cut["frames"]:
        st = cr.member_stations.get(m.uid)
        if not st or not st.get("x"):
            continue
        x = frac * m.length
        vals = {k: _interp_station(st, x, k)
                for k in ("N", "V2", "V3", "T", "M2", "M3")}
        xa, ya, za, _, _ = _local_axes(m)
        xa, ya, za = (np.asarray(v, float) for v in (xa, ya, za))
        Fst = vals["N"] * xa + vals["V2"] * ya + vals["V3"] * za
        Mst = vals["T"] * xa + vals["M2"] * ya + vals["M3"] * za
        # station vector = force on the member's i-side part from its j-side
        # part; the free-body side flips it per cut (see _forces)
        Fon_left = sgn * Fst
        Mon_left = sgn * Mst
        if cut["side"] == "Right":       # free body = left part
            Fb, Mb = Fon_left, Mon_left
        else:                            # free body = right part
            Fb, Mb = -Fon_left, -Mon_left
        R += Fb
        MR += np.cross(r - c, Fb) + Mb
    return R, MR


def compute(engine, asm, sources: Dict[str, object]) -> Dict[str, dict]:
    """results['spandrels']: case/combo -> label -> story -> Left/Right."""
    groups = spandrel_groups(engine, asm)
    if not groups:
        return {}
    for g in groups:
        for side, cut in g["cuts"].items():
            cut["side"] = side
    out: Dict[str, dict] = {}
    for cname, cr in sources.items():
        by_lab: Dict[str, dict] = {}
        for g in groups:
            if g["n_shells"] and not cr.shell_nodal:
                continue
            entry = {"length": float(g["length"]),
                     "n_shells": g["n_shells"], "n_frames": g["n_frames"]}
            for side, cut in g["cuts"].items():
                R, MR = _cut_resultant(cut, cr)
                # internal force on the LEFT material from the right
                F = R if side == "Right" else -R
                M = MR if side == "Right" else -MR
                entry[side] = {
                    "P": float(F @ g["e1"]), "V2": float(F @ g["e2"]),
                    "V3": float(F @ g["e3"]), "T": float(M @ g["e1"]),
                    "M2": float(M @ g["e2"]), "M3": float(M @ g["e3"])}
            by_lab.setdefault(g["label"], {})[g["story"]] = entry
        if by_lab:
            out[cname] = by_lab
    return out
