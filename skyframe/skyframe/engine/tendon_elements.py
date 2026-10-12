"""Engine side of the bonded tendon ELEMENTS (``model_as: "elements"``) —
mechanics in :mod:`skyframe.core.tendon_elements`, CONTRACT "Time-dependent
materials, incremental creep and tendon elements".

Hooks (one line each in the shared engine):

* :func:`build` — at the end of ``OpenSeesEngine._build`` (every build:
  static, P-Delta, modal, pushover ...): adds one ``elasticBeamColumn``
  (Linear transformation with rigid ``-jntOffset`` host-node -> tendon point
  vectors; A = tendon area, E = tendon E, Iy = Iz = A^2/(4 pi), J = 2 Iy)
  per tendon element;
* :func:`apply_pattern` — from ``engine/tendons.apply_pattern_tendons``:
  the initial-stress load pair of every element of a tendon whose pattern
  is applied;
* :func:`augment_report` — results ``tendons[uid]["element"]``.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import openseespy.opensees as ops

from skyframe.core import tendons as _td
from skyframe.core import tendon_elements as _cte

_TOLX = 1e-6


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def has_elements(model) -> bool:
    return any(isinstance(t, dict) and t.get("model_as") == "elements"
               for t in (getattr(model, "tendons", None) or []))


def _frame_node(eng, asm, uid: str, p) -> int:
    """Host-member mesh node (tag) under tendon point ``p``."""
    m = eng._members_by_uid[uid]
    ax = _td._member_axis(m)
    x, _ = _td._project(ax, p)
    x = min(max(x, 0.0), ax[2])
    segs = asm.mesh.segments[uid]
    best, bd = None, math.inf
    for s in segs:
        for xx, n in ((s.x0, s.ni), (s.x0 + s.length, s.nj)):
            if abs(xx - x) < bd:
                best, bd = n, abs(xx - x)
    if bd > 1e-4 * max(1.0, ax[2]):
        raise ValueError(f"tendon element: no host node of {uid!r} under "
                         f"point {tuple(p)}")
    return asm.pz_load_tag.get((uid, best), best + 1)


def _plan(eng, asm, td: dict) -> List[dict]:
    """The element list of one tendon: ``[{"a", "b", "ni", "nj", "P",
    "pieces"}]`` (P = initial piece force at unit pattern scale)."""
    model = eng.model
    pieces = _td.sub_pieces(model, td)
    prof = _td.force_profile(model, td, pieces)
    chords = _cte.chord_points(td, pieces)
    kind = _td.host_kind(model, td)
    if kind == "frame":
        ends = [(_frame_node(eng, asm, pc["host"], a),
                 _frame_node(eng, asm, pc["host"], b))
                for pc, (a, b) in zip(pieces, chords)]
    else:
        hosts = set(td["host"])
        tags = sorted({n + 1 for q in asm.mesh.quads if q.region in hosts
                       for n in q.nodes})
        if not tags:
            raise ValueError(f"Tendon {td['uid']!r}: host shell region(s) "
                             "have no mesh nodes")

        def near(p):
            return min(tags, key=lambda t: (math.dist(asm.node_coords[t], p),
                                            t))
        ends = [(near(a), near(b)) for a, b in chords]
    out: List[dict] = []
    cur: Optional[dict] = None
    for i, ((a, b), (ni, nj)) in enumerate(zip(chords, ends)):
        if cur is not None and cur["nj"] == cur["ni"]:
            # previous run collapsed onto one node: extend it
            cur["b"], cur["nj"] = b, nj
            cur["pieces"].append(i)
            continue
        cur = {"a": a, "b": b, "ni": ni, "nj": nj, "pieces": [i]}
        out.append(cur)
    out = [e for e in out if e["ni"] != e["nj"]]
    for e in out:
        e["P"] = (sum(prof["P_mid"][i] for i in e["pieces"])
                  / len(e["pieces"]))
        ch = _sub(e["b"], e["a"])
        e["L"] = _norm(ch)
        e["t"] = tuple(c / e["L"] for c in ch)
    return out


def build(eng, asm, etag: int) -> int:
    """Add the bonded tendon elements to the live OpenSees domain; returns
    the last element tag used.  No-op (returns ``etag``) without element
    tendons."""
    model = eng.model
    if not has_elements(model):
        return etag
    info: Dict[str, dict] = {}
    for td in _cte.element_tendons(model):
        mat = _td.tendon_material(model, td)
        A = td["area"]
        Ib = A * A / (4.0 * math.pi)
        G = mat.E / (2.0 * (1.0 + getattr(mat, "nu", 0.3)))
        els = _plan(eng, asm, td)
        for e in els:
            xi = asm.node_coords[e["ni"]]
            xj = asm.node_coords[e["nj"]]
            ri = _sub(e["a"], xi)
            rj = _sub(e["b"], xj)
            t = e["t"]
            cand = [(abs(t[k]), k) for k in range(3)]
            k_min = min(cand)[1]
            ref = [0.0, 0.0, 0.0]
            ref[k_min] = 1.0
            # vecxz: any vector not parallel to the chord
            etag += 1
            ops.geomTransf("Linear", etag, *ref, "-jntOffset", *ri, *rj)
            ops.element("elasticBeamColumn", etag, e["ni"], e["nj"], A,
                        mat.E, G, 2.0 * Ib, Ib, Ib, etag)
            e["ele"] = etag
            e["ri"], e["rj"] = ri, rj
            e["k"] = mat.E * A / e["L"]
        info[td["uid"]] = {"td": td, "elements": els}
    eng.__dict__["_tde_info"] = info
    return etag


def _info(eng) -> Dict[str, dict]:
    return eng.__dict__.get("_tde_info") or {}


def apply_pattern(eng, asm, td: dict, scale: float) -> None:
    """Initial-stress loads of one element tendon (scaled): ``+P t`` at the
    start point and ``-P t`` at the end point of every element, moved to
    the host nodes with their transfer couples.  A per-element override
    ``model._tde_override[uid]`` (list of forces, staged relaxation)
    replaces ``P * scale``."""
    ent = _info(eng).get(td["uid"])
    if ent is None:
        return
    ovr = (getattr(eng.model, "_tde_override", None) or {}).get(td["uid"])
    acc: Dict[int, List[float]] = {}
    for k, e in enumerate(ent["elements"]):
        P = ovr[k] if ovr is not None else e["P"] * scale
        if P == 0.0:
            continue
        for node, r, sgn in ((e["ni"], e["ri"], 1.0), (e["nj"], e["rj"],
                                                        -1.0)):
            F = tuple(sgn * P * c for c in e["t"])
            M = _cross(r, F)
            v = acc.setdefault(node, [0.0] * 6)
            for i in range(3):
                v[i] += F[i]
                v[3 + i] += M[i]
    for t in sorted(acc):
        ops.load(t, *acc[t])


def elongations(eng, uid: str, node_disp) -> List[float]:
    """Element elongations (m) from node displacements (small rotations:
    end point displacement ``u + theta x r``)."""
    ent = _info(eng).get(uid)
    if ent is None:
        return []
    out = []
    for e in ent["elements"]:
        ui = np.asarray(node_disp[e["ni"]], dtype=float)
        uj = np.asarray(node_disp[e["nj"]], dtype=float)
        da = ui[:3] + np.cross(ui[3:6], e["ri"])
        db = uj[:3] + np.cross(uj[3:6], e["rj"])
        out.append(float(np.dot(db - da, e["t"])))
    return out


def forces(eng, uid: str, node_disp, pattern_scale: float) -> List[float]:
    """Element axial forces ``scale * P_e + k_e * delta_L`` (tension +)."""
    ent = _info(eng).get(uid)
    if ent is None:
        return []
    dl = elongations(eng, uid, node_disp)
    return [pattern_scale * e["P"] + e["k"] * d
            for e, d in zip(ent["elements"], dl)]


def augment_report(eng, rep: Dict[str, dict]) -> None:
    """Add ``rep[uid]["element"]`` for element tendons: geometry, initial
    forces and the element forces / elastic-shortening loss of every
    static case solved so far (cached results)."""
    if not has_elements(eng.model):
        return
    info = _info(eng)
    if not info:
        try:
            eng._asm if eng._asm is not None else eng._build()
        except Exception:                                # pragma: no cover
            return
        info = _info(eng)
    model = eng.model
    for uid, ent in info.items():
        if uid not in rep:
            continue
        td = ent["td"]
        els = ent["elements"]
        cases = {}
        for cname, cr in sorted(getattr(eng, "_case_cache", {}).items()):
            case = model.cases.get(cname)
            if case is None or not getattr(cr, "node_disp", None):
                continue
            sc = float(case.patterns.get(td["pattern"], 0.0))
            try:
                P = forces(eng, uid, cr.node_disp, sc)
            except KeyError:
                continue
            P0 = [sc * e["P"] for e in els]
            cases[cname] = {"P": P, "P_initial": P0,
                            "loss": [a - b for a, b in zip(P0, P)],
                            "P_mean": float(np.mean(P)) if P else 0.0}
        rep[uid]["element"] = {
            "n": len(els),
            "points": [[list(e["a"]), list(e["b"])] for e in els],
            "length": [e["L"] for e in els],
            "P_initial": [e["P"] for e in els],
            "stiffness": [e["k"] for e in els],
            "cases": cases,
        }
