"""Engine side of multiple towers (CONTRACT "Multiple towers").

Everything here is inert unless ``model.towers`` is non-empty; the hooks in
:mod:`skyframe.engine.opensees_engine` then return the legacy values
unchanged, so single-tower models stay byte-identical.

With towers:

* FE node -> tower: ``joint_towers`` tag, else a majority vote of the
  towers of the frame segments / shell quads / links meeting at the node,
  restricted to towers having a story at the node's elevation (ties ->
  tower order), else plan footprint, else the default tower
  (:func:`node_towers`).
* story node sets keep only the nodes of the story's tower, so every
  (tower, story) gets its OWN rigid diaphragm, master node at the tower's
  plan centre and rotational mass inertia from the tower's plan extents
  (:func:`filter_story_nodes`, :func:`master_xy`, :func:`story_extents`);
  accidental torsion uses the tower's extents too.
* story results per tower (:func:`story_block`): displacement = the
  tower-story master (else its node average); drift chains bottom -> top
  WITHIN the tower, the first story measured from the tower base (0 for a
  tower founded at the ground, i.e. with no story of any tower at its base
  elevation; else the average displacement of the tower's base nodes);
  story shear = the horizontal free-body cut force just below the story
  level through the tower's OWN elements (frame segments, shell quads,
  links crossing the plane, plus reactions of the tower's supports above
  it) — ETABS "story forces" from element forces, so load carried between
  towers by link beams or a podium is attributed where it flows.  For a
  disconnected tower this equals the legacy applied-load story shear by
  equilibrium.
* centre of mass, tributary masses, seismic diagnostics (story stiffness,
  torsional / soft-story irregularity) are the legacy routines run on the
  per-tower model views of :mod:`skyframe.core.towers`.
* time-history / FNA story peaks: drift chains re-evaluated per tower from
  the story displacement series.
"""
from __future__ import annotations

import contextlib
import functools
from collections import Counter
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core import towers as _ct

_CUT_DELTA = 1e-9


def active(eng) -> bool:
    return bool(eng.__dict__.get("_tw"))


# ------------------------------------------------------------- install
def install(eng) -> None:
    """Bind the per-tower overrides on an engine instance (no-op without
    towers)."""
    model = eng.model
    if not _ct.has_towers(model):
        return
    ctx = _ct.context(model)
    vws = {n: _ct.tower_view(model, n, ctx) for n in ctx.names}
    st = dict(ctx.st_tower)
    eng.__dict__["_tw"] = {
        "ctx": ctx, "views": vws, "story_tower": st,
        "center": {n: v.plan_center() for n, v in vws.items()},
        "extents": {n: v.plan_extents() for n, v in vws.items()},
    }
    cls = type(eng)
    d = eng.__dict__
    d["_story_results"] = functools.partial(story_results, eng)
    d["_story_shears"] = functools.partial(_story_shears, eng, cls)
    d["_story_cm"] = functools.partial(_story_cm, eng, cls)
    d["_tributary_masses"] = functools.partial(_tributary_masses, eng, cls)
    d["_seismic_diagnostics"] = functools.partial(_seismic_diagnostics,
                                                  eng, cls)
    d["_modal_static"] = functools.partial(_modal_static, eng, cls)
    d["run_time_history"] = functools.partial(_th_wrap, eng,
                                              cls.run_time_history)
    d["run_fna"] = functools.partial(_th_wrap, eng, cls.run_fna)
    from skyframe.engine import towers_ext as _twx
    _twx.install(eng)                       # follow-ups: staged per tower


@contextlib.contextmanager
def _as_view(eng, view):
    saved = eng.model
    # the uid lookup memo is per model: never let a view's (partial) memo
    # leak into the full model (or the full memo into the view)
    memo = eng.__dict__.pop("_uid_lk", None)
    eng.model = view
    try:
        yield view
    finally:
        eng.model = saved
        eng.__dict__.pop("_uid_lk", None)
        if memo is not None:
            eng.__dict__["_uid_lk"] = memo


def _tower_of_story(eng, story: str) -> Optional[str]:
    tw = eng.__dict__.get("_tw")
    return tw["story_tower"].get(story) if tw else None


# ------------------------------------------------------------- build hooks
def node_towers(eng, asm) -> Dict[int, str]:
    """{struct node tag: tower} (memoised on the assembly)."""
    memo = asm.__dict__.get("_tw_nodes")
    if memo is not None:
        return memo
    ctx = eng.__dict__["_tw"]["ctx"]
    votes: Dict[int, Counter] = {}
    for uid, segs in asm.mesh.segments.items():
        m = eng._members_by_uid.get(uid)
        if m is None:
            continue
        t = ctx.obj(m)
        for seg in segs:
            for idx in (seg.ni, seg.nj):
                votes.setdefault(idx + 1, Counter())[t] += 1
    regions = {r.uid: r for r in eng.model.shells}
    for q in asm.mesh.quads:
        r = regions.get(q.region)
        if r is None:
            continue
        t = ctx.obj(r)
        for idx in q.nodes:
            votes.setdefault(idx + 1, Counter())[t] += 1
    for lk in eng.model.links:
        t = ctx.obj(lk)
        for p in (lk.pi, lk.pj):
            try:
                tag = eng._find_node(asm, p)
            except (ValueError, KeyError):
                continue
            votes.setdefault(tag, Counter())[t] += 1
    out: Dict[int, str] = {}
    for tag, c in asm.struct_coords.items():
        k = _ct._key(c)
        if k in ctx.joint:
            out[tag] = ctx.joint[k]
        else:
            out[tag] = _ct.resolve_votes(ctx, votes.get(tag), c)
    asm.__dict__["_tw_nodes"] = out
    return out


def filter_story_nodes(eng, asm) -> None:
    """Keep only the story's own tower nodes in each story node set."""
    if not active(eng):
        return
    nt = node_towers(eng, asm)
    st = eng.__dict__["_tw"]["story_tower"]
    for s in eng.model.stories:
        tw = st.get(s.name)
        asm.story_nodes[s.name] = [t for t in asm.story_nodes[s.name]
                                   if nt.get(t) == tw]


def master_xy(eng, story: str, cx: float, cy: float) -> Tuple[float, float]:
    tw = _tower_of_story(eng, story)
    if tw is None:
        return cx, cy
    return eng.__dict__["_tw"]["center"][tw]


def story_extents(eng, story: str, lx: float, ly: float
                  ) -> Tuple[float, float]:
    tw = _tower_of_story(eng, story)
    if tw is None:
        return lx, ly
    return eng.__dict__["_tw"]["extents"][tw]


# ------------------------------------------------------- view-run overrides
def _story_shears(eng, cls, case):
    """Applied-load story shears per tower (pushover distribution etc.)."""
    out: dict = {}
    for _, v in eng.__dict__["_tw"]["views"].items():
        with _as_view(eng, v):
            out.update(cls._story_shears(eng, case))
    return {s.name: out.get(s.name, (0.0, 0.0)) for s in eng.model.stories}


def _story_cm(eng, cls):
    out: dict = {}
    for _, v in eng.__dict__["_tw"]["views"].items():
        with _as_view(eng, v):
            out.update(cls._story_cm(eng))
    return {s.name: out[s.name] for s in eng.model.stories if s.name in out}


def _tributary_masses(eng, cls, asm):
    out: Dict[int, float] = {}
    for _, v in eng.__dict__["_tw"]["views"].items():
        with _as_view(eng, v):
            part = cls._tributary_masses(eng, asm)
        for t, m in part.items():
            out[t] = out.get(t, 0.0) + m
    return out


def _seismic_diagnostics(eng, cls, asm, results_by_name):
    stiff: dict = {}
    irr: dict = {}
    for _, v in eng.__dict__["_tw"]["views"].items():
        with _as_view(eng, v):
            s_part, i_part = cls._seismic_diagnostics(eng, asm,
                                                      results_by_name)
        for c, by in s_part.items():
            stiff.setdefault(c, {}).update(by)
        for c, by in i_part.items():
            irr.setdefault(c, {}).update(by)
    order = [s.name for s in eng.model.stories]
    cases = [c for c in results_by_name if c in stiff]
    stiff = {c: {s: stiff[c][s] for s in order if s in stiff[c]}
             for c in cases}
    irr = {c: {s: irr[c][s] for s in order if s in irr.get(c, {})}
           for c in cases}
    return stiff, irr


# ------------------------------------------------------------ story results
def story_results(eng, asm, case, node_disp):
    eng.__dict__.pop("_lat_capture", None)       # legacy capture unused
    return story_block(eng, asm, node_disp)


def _modal_static(eng, cls, loads):
    cr = cls._modal_static(eng, loads)
    asm = eng._asm
    cr.story = story_block(eng, asm, cr.node_disp)
    return cr


def _base_disp(eng, asm, tower: str, node_disp) -> Tuple[float, float]:
    """Displacement of the tower base: 0 for a tower founded at the ground
    (no story of any tower at its base elevation), else the mean of the
    tower's base nodes (lower ends of its elements at the base)."""
    model = eng.model
    base = float(_ct.tower_entry(model, tower).get("base_elevation", 0.0))
    if not any(abs(s.elevation - base) < 1e-6 for s in model.stories):
        return 0.0, 0.0
    ctx = eng.__dict__["_tw"]["ctx"]
    pts = asm.mesh.points
    tags = set()
    for uid, segs in asm.mesh.segments.items():
        m = eng._members_by_uid.get(uid)
        if m is None or ctx.obj(m) != tower:
            continue
        for seg in segs:
            zi, zj = float(pts[seg.ni][2]), float(pts[seg.nj][2])
            for idx, z, zo in ((seg.ni, zi, zj), (seg.nj, zj, zi)):
                if abs(z - base) < 1e-6 and zo > base + 1e-6:
                    tags.add(idx + 1)
    tags = [t for t in tags if t in node_disp]
    if not tags:
        return 0.0, 0.0
    return (sum(node_disp[t][0] for t in tags) / len(tags),
            sum(node_disp[t][1] for t in tags) / len(tags))


def story_block(eng, asm, node_disp) -> Dict[str, Dict[str, float]]:
    """Per-tower story displacement / drift / cut shear (model order)."""
    model = eng.model
    st = eng.__dict__["_tw"]["story_tower"]
    shears = tower_cut_shears(eng, asm)
    out: Dict[str, Dict[str, float]] = {}
    for tw in eng.__dict__["_tw"]["ctx"].names:
        prev_ux, prev_uy = _base_disp(eng, asm, tw, node_disp)
        for s in model.stories:
            if st[s.name] != tw:
                continue
            if s.name in asm.masters:
                d = node_disp[asm.masters[s.name]]
                ux, uy = d[0], d[1]
            else:
                nodes = asm.story_nodes.get(s.name) or []
                ux = (sum(node_disp[t][0] for t in nodes) / len(nodes)
                      if nodes else 0.0)
                uy = (sum(node_disp[t][1] for t in nodes) / len(nodes)
                      if nodes else 0.0)
            h = s.height if s.height > 0 else 1.0
            vx, vy = shears.get(s.name, (0.0, 0.0))
            out[s.name] = {"ux": ux, "uy": uy,
                           "drift_x": (ux - prev_ux) / h,
                           "drift_y": (uy - prev_uy) / h,
                           "shear_x": vx, "shear_y": vy}
            prev_ux, prev_uy = ux, uy
    return {s.name: out[s.name] for s in model.stories if s.name in out}


def tower_cut_shears(eng, asm) -> Dict[str, Tuple[float, float]]:
    """{story: (vx, vy)} — the horizontal free-body cut force on the plane
    just below each story level (z = elevation - 1e-9) through the story
    tower's OWN elements, from the CURRENT domain state (same element
    rules as :func:`skyframe.engine.story_shear.cut_shear`, filtered by
    tower; links crossing the plane are added from their global end
    forces)."""
    from skyframe.engine.opensees_engine import _local_axes
    model = eng.model
    ctx = eng.__dict__["_tw"]["ctx"]
    st = eng.__dict__["_tw"]["story_tower"]
    planes: Dict[str, List[Tuple[str, float]]] = {}
    for s in model.stories:
        planes.setdefault(st[s.name], []).append(
            (s.name, s.elevation - _CUT_DELTA))
    acc: Dict[str, List[float]] = {s.name: [0.0, 0.0] for s in model.stories}
    pts = asm.mesh.points
    for uid, segs in asm.mesh.segments.items():
        member = eng._members_by_uid.get(uid)
        if member is None:
            continue
        pl = planes.get(ctx.obj(member))
        if not pl:
            continue
        R = None
        for seg in segs:
            zi, zj = float(pts[seg.ni][2]), float(pts[seg.nj][2])
            lo_z, hi_z = min(zi, zj), max(zi, zj)
            hits = [(n, zc) for n, zc in pl if lo_z < zc < hi_z]
            if not hits:
                continue
            if R is None:
                xax, yax, zax, _, _ = _local_axes(member)
                R = np.array([xax, yax, zax])
            etag = asm.seg_ele[(uid, seg.index)]
            loc = np.asarray(ops.eleResponse(etag, "localForce"), float)
            if len(loc) == 12:
                loc = loc + eng._seg_fef.get((uid, seg.index), np.zeros(12))
                up = 6 if zj > zi else 0
                fg = R.T @ loc[up:up + 3]
            else:
                g = np.asarray(ops.eleResponse(etag, "forces"), float)
                half = len(g) // 2
                fg = g[half:half + 3] if zj > zi else g[0:3]
            L = seg.length
            for name, zc in hits:
                vx, vy = float(fg[0]), float(fg[1])
                xc = (zc - zi) * L / (zj - zi)
                a0, b0 = (xc, L) if zj > zi else (0.0, xc)
                for rec in eng._seg_span_loads.get((uid, seg.index), ()):
                    if rec[0] == "point":
                        if a0 <= rec[2] <= b0:
                            g = R.T @ np.asarray(rec[1], float)
                            vx += g[0]
                            vy += g[1]
                    elif rec[0] == "trap":
                        _, wa, wb, xa, xb = rec
                        a_, b_ = max(xa, a0), min(xb, b0)
                        if b_ <= a_:
                            continue
                        wa_, wb_ = np.asarray(wa, float), np.asarray(wb,
                                                                     float)
                        ta, tb = (a_ - xa) / (xb - xa), (b_ - xa) / (xb - xa)
                        w = 0.5 * ((wa_ + (wb_ - wa_) * ta)
                                   + (wa_ + (wb_ - wa_) * tb)) * (b_ - a_)
                        g = R.T @ w
                        vx += g[0]
                        vy += g[1]
                acc[name][0] += vx
                acc[name][1] += vy
    regions = {r.uid: r for r in model.shells}
    for sq, qtag in zip(asm.shell_quads, asm.quad_ele):
        r = regions.get(sq.get("region"))
        pl = planes.get(ctx.obj(r)) if r is not None else None
        if not pl:
            continue
        tags = list(sq["nodes"])
        zs = [asm.node_coords[t][2] for t in tags]
        hits = [(n, zc) for n, zc in pl if min(zs) < zc < max(zs)]
        if not hits:
            continue
        f = np.asarray(ops.eleResponse(qtag, "forces"), float)
        for name, zc in hits:
            for k, t in enumerate(tags):
                if zs[k] > zc:
                    acc[name][0] += f[6 * k]
                    acc[name][1] += f[6 * k + 1]
    for lk in model.links:
        etag = asm.link_ele.get(lk.uid)
        pl = planes.get(ctx.obj(lk))
        if etag is None or not pl:
            continue
        zi, zj = float(lk.pi[2]), float(lk.pj[2])
        hits = [(n, zc) for n, zc in pl if min(zi, zj) < zc < max(zi, zj)]
        if not hits:
            continue
        g = np.asarray(ops.eleResponse(etag, "globalForce"), float)
        half = len(g) // 2
        fg = g[half:half + 3] if zj > zi else g[0:3]
        for name, _ in hits:
            acc[name][0] += float(fg[0])
            acc[name][1] += float(fg[1])
    nt = node_towers(eng, asm)
    sup = [t for t in asm.support_tags if t in asm.node_coords]
    if sup:
        ops.reactions()
        for t in sup:
            pl = planes.get(nt.get(t))
            if not pl:
                continue
            c = asm.node_coords[t]
            hits = [n for n, zc in pl if c[2] > zc]
            if not hits:
                continue
            r = ops.nodeReaction(t)
            for name in hits:
                acc[name][0] -= r[0]
                acc[name][1] -= r[1]
    return {k: (float(v[0]), float(v[1])) for k, v in acc.items()}


# ------------------------------------------------------- TH / FNA drifts
def _th_wrap(eng, fn, name):
    res = fn(eng, name)
    try:
        _fix_th_drifts(eng, res)
    except (KeyError, AttributeError):                  # pragma: no cover
        pass
    return res


def _fix_th_drifts(eng, res) -> None:
    """Re-chain the TH peak story drifts within each tower."""
    model = eng.model
    st = eng.__dict__["_tw"]["story_tower"]
    peaks = res.peaks.get("story") or {}
    n = len(res.t)
    for tw in eng.__dict__["_tw"]["ctx"].names:
        prev_x = [0.0] * n
        prev_y = [0.0] * n
        for s in model.stories:
            if st[s.name] != tw or s.name not in peaks:
                continue
            ux, uy = res.story_ux[s.name], res.story_uy[s.name]
            h = s.height if s.height > 0 else 1.0

            def pk(vals):
                return max(abs(v) for v in vals) if len(vals) else 0.0
            peaks[s.name]["drift_x"] = pk([(u - p) / h
                                           for u, p in zip(ux, prev_x)])
            peaks[s.name]["drift_y"] = pk([(u - p) / h
                                           for u, p in zip(uy, prev_y)])
            prev_x, prev_y = ux, uy


# ---------------------------------------------------------------- results
def attach(eng, res) -> None:
    """``results["towers"]`` summary: per tower its stories, base
    elevation, grid system and the per-case base shear (sum of the shears
    of the tower's lowest story)."""
    if not active(eng):
        return
    model = eng.model
    summ = _ct.summary(model)
    for tw, ent in summ.items():
        first = ent["stories"][0] if ent["stories"] else None
        bs: Dict[str, Dict[str, float]] = {}
        for group in (res.cases, res.combos, res.rs_cases):
            for cname, cr in group.items():
                row = cr.story.get(first) if first else None
                if row is not None:
                    bs[cname] = {"FX": float(row["shear_x"]),
                                 "FY": float(row["shear_y"])}
        ent["base_shear"] = bs
    res.towers = summ
    res.story_tower = dict(eng.__dict__["_tw"]["story_tower"])
