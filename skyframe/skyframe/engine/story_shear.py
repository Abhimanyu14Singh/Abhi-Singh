"""Story shear from every lateral load type (engine side).

CONTRACT "Story shear from all lateral load types".

ETABS story shear at a level = the sum of the lateral (global X / Y) forces
applied at and above that level — equivalently the horizontal cut force
just below it.  The legacy ``OpenSeesEngine._story_shears`` sums the
pattern ``story_forces`` and ``nodal_loads`` (kept verbatim, so models whose
lateral loads are only those stay byte-identical).  This module adds every
OTHER lateral load, taken from what is ACTUALLY put on the OpenSees domain:

* :func:`capture` wraps the pattern application of a case.  It intercepts
  ``ops.load`` while the engine is inside ``_apply_member_load``,
  ``_apply_area_load`` or the tendon loader (member point loads landing on
  a node, directional / joint-pattern / shell-load-set area loads,
  shell-hosted tendon forces ...) and records each nodal force with its
  node; the fixed-end ("consistent") nodal forces of member SPAN loads
  (``_apply_fef``) and all thermal forces (``_apply_thermal`` and the
  gradient / joint / shell temperature loads, which are not wrapped at all)
  are NOT recorded: span loads are integrated exactly instead (below) and
  temperature loads are self-equilibrated imposed strains (zero story
  shear).  Story forces and pattern nodal loads are outside the wrapped
  contexts (the legacy sum counts them).  Ground displacements are imposed
  ``sp`` values (reactions only, nothing to count).
* every member span load the engine records in ``_seg_span_loads``
  (``beamUniform`` / ``beamPoint`` element loads AND fixed-end-path partial
  UDL / trapezoid / point loads; member-local components on segment-local
  x) is converted to global and its horizontal part integrated EXACTLY over
  the part of the segment at or above each level: a segment from z_i to
  z_j crossing elevation e is cut at x_e = L (e - z_i)/(z_j - z_i); a
  linear load w(x) contributes the closed-form integral of w over
  [x_a, x_b] intersected with the upper part; a point load counts when its
  station lies at or above e.
* a horizontal component smaller than 1e-12 of the load's own magnitude
  (rounding noise of a gravity load transformed through member axes) is
  dropped, so gravity-only member / area loads add exactly nothing.

When no capture of the case is available (e.g. a nonlinear static case's
final state), :func:`extra_shears` REPLAYS the pattern application with
``ops.load`` / ``ops.eleLoad`` / ``ops.sp`` stubbed out (the domain is not
touched; the engine's span-load bookkeeping is saved and restored).
"""

from __future__ import annotations

import contextlib
import math
import warnings
from typing import Dict, List, Optional, Tuple

import openseespy.opensees as ops

_NOISE = 1e-12
_WRAP_RECORD = ("_apply_member_load", "_apply_area_load")
_WRAP_MUTE = ("_apply_fef", "_apply_thermal")


class LateralCapture:
    """Loads of one case: recorded nodal forces + member span records."""

    def __init__(self, patterns: Dict[str, float]):
        self.patterns = dict(patterns)
        self.nodal: List[Tuple[int, float, float, float]] = []
        self.spans: List[Tuple[str, int, tuple]] = []


@contextlib.contextmanager
def capture(eng, patterns: Dict[str, float], passthrough: bool = True):
    """Record the lateral loads applied inside the ``with`` block.

    ``passthrough=False`` (replay) stubs the OpenSees load commands so the
    domain is untouched.  The result is stored on ``eng._lat_capture``
    (passthrough) and also yielded."""
    from skyframe.engine import tendons as _tdn
    cap = LateralCapture(patterns)
    state = {"rec": 0, "mute": 0}
    orig = {"load": ops.load, "eleLoad": ops.eleLoad, "sp": ops.sp}

    def load(tag, *vals):
        if state["rec"] and not state["mute"] and len(vals) >= 3:
            cap.nodal.append((int(tag), float(vals[0]), float(vals[1]),
                              float(vals[2])))
        if passthrough:
            return orig["load"](tag, *vals)
        return None

    def wrap(fn, key):
        def inner(*a, **k):
            state[key] += 1
            try:
                return fn(*a, **k)
            finally:
                state[key] -= 1
        return inner

    saved_inst = {}
    for name in _WRAP_RECORD + _WRAP_MUTE:
        if name in eng.__dict__:
            saved_inst[name] = eng.__dict__[name]
        eng.__dict__[name] = wrap(getattr(eng, name),
                                  "rec" if name in _WRAP_RECORD else "mute")
    orig_tdn = _tdn.apply_pattern_tendons
    _tdn.apply_pattern_tendons = wrap(orig_tdn, "rec")
    spans0 = getattr(eng, "_seg_span_loads", None) or {}
    before = {k: len(v) for k, v in spans0.items()}
    saved_bk = None
    if not passthrough:
        saved_bk = (eng._seg_span_loads, eng._seg_fef)
        eng._seg_span_loads, eng._seg_fef = {}, {}
        before = {}
    ops.load = load
    if not passthrough:
        ops.eleLoad = lambda *a, **k: None
        ops.sp = lambda *a, **k: None
    ok = False
    try:
        yield cap
        ok = True
    finally:
        ops.load, ops.eleLoad, ops.sp = (orig["load"], orig["eleLoad"],
                                         orig["sp"])
        _tdn.apply_pattern_tendons = orig_tdn
        for name in _WRAP_RECORD + _WRAP_MUTE:
            if name in saved_inst:
                eng.__dict__[name] = saved_inst[name]
            else:
                eng.__dict__.pop(name, None)
        spans = getattr(eng, "_seg_span_loads", None) or {}
        for key in sorted(spans, key=lambda k: (str(k[0]), k[1])):
            for rec in spans[key][before.get(key, 0):]:
                cap.spans.append((key[0], key[1], rec))
        if saved_bk is not None:
            eng._seg_span_loads, eng._seg_fef = saved_bk
        if ok and passthrough:
            eng.__dict__["_lat_capture"] = cap


def _replay(eng, asm, patterns: Dict[str, float]) -> LateralCapture:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with capture(eng, patterns, passthrough=False) as cap:
            for pat_name, scale in patterns.items():
                eng._apply_pattern(asm, pat_name, scale)
    return cap


def _upper_interval(zi: float, zj: float, L: float, e: float,
                    tol: float) -> Optional[Tuple[float, float]]:
    """Segment-local x interval of the part at or above elevation e."""
    dz = zj - zi
    if abs(dz) <= tol:
        return (0.0, L) if min(zi, zj) >= e - tol else None
    xe = (e - zi) * L / dz
    lo, hi = (xe, L) if dz > 0.0 else (0.0, xe)
    lo, hi = max(lo, 0.0), min(hi, L)
    if hi - lo <= 0.0:
        return None
    return lo, hi


def _horiz(axes, v) -> Tuple[float, float, float]:
    """(gx, gy, |v|) of a member-local vector."""
    xax, yax, zax = axes
    gx = v[0] * xax[0] + v[1] * yax[0] + v[2] * zax[0]
    gy = v[0] * xax[1] + v[1] * yax[1] + v[2] * zax[1]
    return gx, gy, math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def lateral_shears(eng, asm, cap: LateralCapture
                   ) -> Dict[str, Tuple[float, float]]:
    """{story: (vx, vy)} of the captured (non-legacy) lateral loads; only
    stories with a non-zero contribution appear."""
    from skyframe.engine.opensees_engine import _TOL, _local_axes
    model = eng.model
    stories = [(s.name, s.elevation) for s in model.stories]
    out: Dict[str, List[float]] = {}

    def add(name, fx, fy):
        acc = out.setdefault(name, [0.0, 0.0])
        acc[0] += fx
        acc[1] += fy

    for tag, fx, fy, fz in cap.nodal:
        mag = math.sqrt(fx * fx + fy * fy + fz * fz)
        if max(abs(fx), abs(fy)) <= _NOISE * mag:
            continue
        c = asm.node_coords.get(tag)
        if c is None:                                   # pragma: no cover
            continue
        for name, e in stories:
            if c[2] >= e - _TOL:
                add(name, fx, fy)

    axes_cache: Dict[str, tuple] = {}
    pts = asm.mesh.points
    for uid, idx, rec in cap.spans:
        kind = rec[0]
        if kind not in ("trap", "point"):
            continue                      # moment / kappa: no force
        member = eng._members_by_uid[uid]
        axes = axes_cache.get(uid)
        if axes is None:
            xax, yax, zax, _, _ = _local_axes(member)
            axes = axes_cache[uid] = (xax, yax, zax)
        seg = asm.mesh.segments[uid][idx]
        zi, zj = float(pts[seg.ni][2]), float(pts[seg.nj][2])
        L = seg.length
        if kind == "point":
            gx, gy, mag = _horiz(axes, rec[1])
            if max(abs(gx), abs(gy)) <= _NOISE * mag:
                continue
            xi = rec[2]
            z = zi + (zj - zi) * (xi / L if L > 0.0 else 0.0)
            for name, e in stories:
                if z >= e - _TOL:
                    add(name, gx, gy)
            continue
        _, wa, wb, xa, xb = rec
        ga = _horiz(axes, wa)
        gb = _horiz(axes, wb)
        if (max(abs(ga[0]), abs(ga[1]), abs(gb[0]), abs(gb[1]))
                <= _NOISE * max(ga[2], gb[2])) or xb - xa <= 0.0:
            continue
        for name, e in stories:
            iv = _upper_interval(zi, zj, L, e, _TOL)
            if iv is None:
                continue
            lo, hi = max(xa, iv[0]), min(xb, iv[1])
            if hi - lo <= 0.0:
                continue
            ta, tb = (lo - xa) / (xb - xa), (hi - xa) / (xb - xa)
            fx = 0.5 * ((ga[0] + (gb[0] - ga[0]) * ta)
                        + (ga[0] + (gb[0] - ga[0]) * tb)) * (hi - lo)
            fy = 0.5 * ((ga[1] + (gb[1] - ga[1]) * ta)
                        + (ga[1] + (gb[1] - ga[1]) * tb)) * (hi - lo)
            add(name, fx, fy)
    return {k: (v[0], v[1]) for k, v in out.items()
            if v[0] != 0.0 or v[1] != 0.0}


def extra_shears(eng, asm, patterns: Dict[str, float]
                 ) -> Dict[str, Tuple[float, float]]:
    """Non-legacy lateral story shears of a case (consumes the capture of
    that case when present, else replays the pattern application)."""
    cap = eng.__dict__.pop("_lat_capture", None)
    if cap is None or cap.patterns != dict(patterns):
        cap = _replay(eng, asm, patterns)
    return lateral_shears(eng, asm, cap)


def add_to(shears: Dict[str, Tuple[float, float]],
           extra: Dict[str, Tuple[float, float]]
           ) -> Dict[str, Tuple[float, float]]:
    """Legacy shears + extras (stories without extras untouched)."""
    if not extra:
        return shears
    out = dict(shears)
    for name, (ex, ey) in extra.items():
        if name in out:
            vx, vy = out[name]
            out[name] = (vx + ex, vy + ey)
    return out


# ------------------------------------------------------------ validation
def cut_shear(eng, asm, elevation: float, delta: float = 1e-9
              ) -> Tuple[float, float]:
    """Independent check: the horizontal cut force on a plane just below
    ``elevation`` (z_c = elevation - delta) from the CURRENT domain state.

    = sum over frame segments crossing the plane of the force the lower
    part exerts on the upper part, plus the same for shell elements, i.e.
    for each crossing element the global end forces at its upper node(s)
    plus (frames) the span load on the piece between the plane and the
    upper node, all summed and with the reactions of supports above the
    plane added.  Equals the story shear by equilibrium of the free body
    above the plane."""
    import numpy as np
    from skyframe.engine.opensees_engine import _local_axes
    zc = elevation - delta
    vx = vy = 0.0
    pts = asm.mesh.points
    for uid, segs in asm.mesh.segments.items():
        member = eng._members_by_uid.get(uid)
        if member is None:
            continue
        xax, yax, zax, _, _ = _local_axes(member)
        R = np.array([xax, yax, zax])           # rows: local axes in global
        for seg in segs:
            zi, zj = float(pts[seg.ni][2]), float(pts[seg.nj][2])
            if not (min(zi, zj) < zc < max(zi, zj)):
                continue
            etag = asm.seg_ele[(uid, seg.index)]
            loc = np.asarray(ops.eleResponse(etag, "localForce"), float)
            if len(loc) == 12:
                loc = loc + eng._seg_fef.get((uid, seg.index), np.zeros(12))
                up = 6 if zj > zi else 0
                fg = R.T @ loc[up:up + 3]
            else:                       # truss etc.: global end forces
                g = np.asarray(ops.eleResponse(etag, "forces"), float)
                half = len(g) // 2
                fg = g[half:half + 3] if zj > zi else g[0:3]
            vx += fg[0]
            vy += fg[1]
            # span load on the piece between the plane and the upper node
            L = seg.length
            xc = (zc - zi) * L / (zj - zi)
            lo, hi = (xc, L) if zj > zi else (0.0, xc)
            for rec in eng._seg_span_loads.get((uid, seg.index), ()):
                if rec[0] == "point":
                    if lo <= rec[2] <= hi:
                        g = R.T @ np.asarray(rec[1], float)
                        vx += g[0]
                        vy += g[1]
                elif rec[0] == "trap":
                    _, wa, wb, xa, xb = rec
                    a_, b_ = max(xa, lo), min(xb, hi)
                    if b_ <= a_:
                        continue
                    wa_, wb_ = np.asarray(wa, float), np.asarray(wb, float)
                    ta, tb = (a_ - xa) / (xb - xa), (b_ - xa) / (xb - xa)
                    w = 0.5 * ((wa_ + (wb_ - wa_) * ta)
                               + (wa_ + (wb_ - wa_) * tb)) * (b_ - a_)
                    g = R.T @ w
                    vx += g[0]
                    vy += g[1]
    for sq, qtag in zip(asm.shell_quads, asm.quad_ele):
        tags = list(sq["nodes"])
        zs = [asm.node_coords[t][2] for t in tags]
        if not (min(zs) < zc < max(zs)):
            continue
        f = np.asarray(ops.eleResponse(qtag, "forces"), float)
        for k, t in enumerate(tags):
            if zs[k] > zc:
                vx += f[6 * k]
                vy += f[6 * k + 1]
    ops.reactions()
    for t in asm.support_tags:
        c = asm.node_coords.get(t)
        if c is not None and c[2] > zc:
            r = ops.nodeReaction(t)
            vx -= r[0]
            vy -= r[1]
    return float(vx), float(vy)
