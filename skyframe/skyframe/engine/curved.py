"""Curved frames -- results stitching (see :mod:`skyframe.core.curved`).

The engine analyses the chord-expanded model (``model.curved_info`` set by
:func:`skyframe.core.shellopts.expand_for_analysis`).  ``stitch`` turns the
chords' station tables of one result block back into ONE table per drawn
member along the arc coordinate ``s``::

    {"s": [...],                      # arc length from end i (m)
     "N", "V2", "V3", "T", "M2", "M3": [...],   # chord local axes
     "chord": [k, ...],               # chord index of each station
     "nodes": {"s": [...], "disp": [[ux, uy, uz, rx, ry, rz], ...]}}

A chord station at chord distance ``x`` maps to ``s = s_k + x * (S/n) /
c`` (``c`` chord length, ``S`` arc length).  Stations at a chord joint
appear twice (end of chord k, start of chord k+1: the left / right
values at the kink).
"""

from __future__ import annotations

from typing import Dict, Optional

_KEYS = ("N", "V2", "V3", "T", "M2", "M3")


def has_curved(engine) -> bool:
    return bool(getattr(engine.model, "curved_info", None))


def stitch(engine, cr, uid: str) -> Optional[dict]:
    """Stitched table of drawn curved member ``uid`` in result block
    ``cr`` (a CaseResults), or None when its chords are absent."""
    info = engine.model.curved_info[uid]
    n = info["segments"]
    S = info["arc_length"]
    out: Dict[str, list] = {"s": [], "chord": []}
    for key in _KEYS:
        out[key] = []
    for k, cu in enumerate(info["chords"]):
        st = cr.member_stations.get(cu)
        if st is None:
            return None
        f = (S / n) / info["chord_lengths"][k]
        s0 = info["s_nodes"][k]
        for i, x in enumerate(st["x"]):
            out["s"].append(s0 + x * f)
            out["chord"].append(k)
            for key in _KEYS:
                out[key].append(st[key][i])
    disp = []
    asm = getattr(engine, "_asm", None)
    if asm is not None and cr.node_disp:
        for p in info["points"]:
            try:
                tag = engine._find_node(asm, tuple(p))
            except Exception:                       # pragma: no cover
                disp = []
                break
            disp.append(list(cr.node_disp.get(tag, [0.0] * 6)))
    out["nodes"] = {"s": list(info["s_nodes"]), "disp": disp}
    return out


def _block(engine, cr, uid: str) -> Optional[dict]:
    st = stitch(engine, cr, uid)
    if st is not None and getattr(cr, "minima", None) is not None:
        mn = stitch(engine, cr.minima, uid)
        if mn is not None:
            st["min"] = mn
    return st


def attach(engine, res) -> None:
    """Fill ``res.curved_frames`` (and tag chord rows of ``res.members``
    with ``curved_parent``).  No-op for models without curved members."""
    info = getattr(engine.model, "curved_info", None)
    if not info:
        return
    parent = {c: u for u, ci in info.items() for c in ci["chords"]}
    for row in res.members:
        if row.get("uid") in parent:
            row["curved_parent"] = parent[row["uid"]]
    out: Dict[str, dict] = {}
    for uid, ci in info.items():
        entry = {k: ci[k] for k in ("chords", "points", "s_nodes",
                                    "arc_length", "radius", "sweep_deg",
                                    "center", "plane_normal", "segments",
                                    "local2", "story", "section", "kind")}
        for grp in ("cases", "combos", "rs_cases"):
            blocks = {}
            for name, cr in (getattr(res, grp, None) or {}).items():
                st = _block(engine, cr, uid)
                if st is not None:
                    blocks[name] = st
            entry[grp] = blocks
        out[uid] = entry
    res.curved_frames = out
