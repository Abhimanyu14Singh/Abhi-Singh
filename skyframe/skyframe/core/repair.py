"""Model repair — ETABS *Check Model* with automatic fixes.

Every fix is a PURE function ``model -> (new_model, report)`` of the model's
JSON form (:meth:`BuildingModel.to_dict`; a :class:`BuildingModel` is also
accepted and then a validated :class:`BuildingModel` is returned).  The
input is never mutated, and a fix that finds nothing to do returns a model
that is byte-for-byte the input.  Every fix is idempotent:
``f(f(m)) == f(m)``.

The fixes build on :mod:`skyframe.core.edit` (joint remapping, member
splitting with load carry-over, dependent-reference cleanup) and on the
geometry searches of :mod:`skyframe.core.checks` (spatial hash, segment
closest points), so they repair exactly what Check Model reports:

========================== ================================================
fix id                     what it does (Check Model code it repairs)
========================== ================================================
``merge_joints``           joints closer than the tolerance are merged
                           (JOINT_COINCIDENT); every reference follows:
                           frame / link ends, shell corners, supports,
                           springs, nodal masses / loads, ground
                           displacements, joint temperatures, joint
                           diaphragms / panel zones, line-spring ends,
                           group points, case output / control points
``remove_zero_length``     frames shorter than the tolerance are deleted
                           (FRAME_ZERO_LENGTH); their two joints are merged
                           first so the connectivity they provided stays
``remove_duplicates``      frames on the same two joints, collinear
                           overlapping frames (split at each other's ends,
                           then de-duplicated) and shells on the same
                           corners (FRAME_DUPLICATE / FRAME_OVERLAP /
                           SHELL_DUPLICATE); the kept object takes over the
                           group / tendon / pushover references
``snap_shell_corners``     shell corners within the tolerance of a frame /
                           link end (else of another shell's corner) move
                           onto it so the meshes connect; a snap that would
                           warp or collapse a shell is skipped
``split_at_joints``        frames are divided at every joint lying on their
                           span (FRAME_JOINT_ON_SPAN); member loads carry
                           over (point loads to the piece that holds them,
                           distributed / trapezoidal loads split by
                           position)
``split_at_crossings``     frames crossing within the tolerance are both
                           divided at the crossing (FRAME_INTERSECTION)
``remove_unconnected_joints`` point records (supports, springs, masses,
                           nodal loads, ...) at a point that is no joint of
                           the analysis mesh are deleted (JOINT_UNCONNECTED)
``report_mechanisms``      REPORT ONLY: the stability diagnostics
                           (near-zero-stiffness mechanisms) and the
                           connectivity errors of Check Model
========================== ================================================

:func:`repair` runs a list of fixes in the canonical order above.  See
CONTRACT.md "Model repair" for the JSON shapes.
"""

from __future__ import annotations

import copy
import functools
import math
import types
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from skyframe.core import checks as _ck
from skyframe.core import edit as _ed

DEFAULT_TOLERANCE = _ck.DEFAULT_TOLERANCE      # 1e-3 m, as Check Model
MAX_TOLERANCE = 1.0                            # m
MAX_PASSES = 20                                # fixed-point iteration cap

REPAIR_FIXES = ("merge_joints", "remove_zero_length", "remove_duplicates",
                "snap_shell_corners", "split_at_joints", "split_at_crossings",
                "remove_unconnected_joints", "report_mechanisms")
# applied when the request names no fix: every geometric fix except the
# opt-in crossing split (ETABS: "when requested") and the report
DEFAULT_FIXES = ("merge_joints", "remove_zero_length", "remove_duplicates",
                 "snap_shell_corners", "split_at_joints",
                 "remove_unconnected_joints")
REPORT_ONLY = ("report_mechanisms",)
FIX_TITLES = {
    "merge_joints": "Merge coincident / near joints",
    "remove_zero_length": "Remove zero-length frames",
    "remove_duplicates": "Remove duplicate / overlapping objects",
    "snap_shell_corners": "Snap shell corners to joints",
    "split_at_joints": "Split frames at joints on their span",
    "split_at_crossings": "Split frames at frame crossings",
    "remove_unconnected_joints": "Remove unconnected joints",
    "report_mechanisms": "Report mechanisms (no change)",
}
# Check Model connectivity codes surfaced by report_mechanisms
_INSTABILITY_CODES = ("NO_SUPPORTS", "FRAME_UNCONNECTED", "SHELL_UNCONNECTED",
                      "LINK_UNCONNECTED", "STRUCTURE_UNSUPPORTED_PART",
                      "STORY_NO_SUPPORT_PATH")

_key = _ed._key
_clean = _ed._clean


# --------------------------------------------------------------------------- #
# report plumbing
# --------------------------------------------------------------------------- #
class _Rep:
    """Per-fix action list (the dry-run preview rows)."""

    def __init__(self, fix: str):
        self.fix = fix
        self.actions: List[dict] = []

    def add(self, message: str, objects: Sequence = (), location=None,
            **extra) -> None:
        objs: List[str] = []
        for o in objects:
            if o not in objs:
                objs.append(o)
        row = {"fix": self.fix, "message": message,
               "objects": objs[:_ck.MAX_OBJECTS_LISTED],
               "location": (None if location is None else
                            [round(float(v), 6) + 0.0 for v in location])}
        row.update(extra)
        self.actions.append(row)


def _fix_report(fix: str, rep: _Rep, ctx: Optional[_ed._Ctx],
                changed: bool, extra: Optional[dict] = None) -> dict:
    out = {"fix": fix, "title": FIX_TITLES[fix], "changed": bool(changed),
           "count": len(rep.actions), "actions": rep.actions,
           "warnings": list(ctx.warnings) if ctx is not None else [],
           "created": ctx.created if ctx is not None else _empty_lists(),
           "deleted": ctx.deleted if ctx is not None else _empty_lists(),
           "modified": (ctx.modified if ctx is not None else
                        {"members": [], "shells": [], "links": []})}
    out.update(extra or {})
    return out


def _empty_lists():
    return {"members": [], "shells": [], "links": [], "points": []}


def _tol(tolerance) -> float:
    if (isinstance(tolerance, bool)
            or not isinstance(tolerance, (int, float))
            or not math.isfinite(tolerance)
            or not 0.0 < float(tolerance) <= MAX_TOLERANCE):
        raise ValueError(f"'tolerance' must be a number in (0, "
                         f"{MAX_TOLERANCE:g}] m")
    return float(tolerance)


def _model_api(fn: Callable) -> Callable:
    """Accept a model dict OR a BuildingModel; never mutate the input; a
    fix that changed nothing returns the input content unchanged."""
    @functools.wraps(fn)
    def wrapper(model, tolerance: float = DEFAULT_TOLERANCE, **kw):
        from skyframe.core.model import BuildingModel
        is_obj = isinstance(model, BuildingModel)
        src = model.to_dict() if is_obj else model
        if not isinstance(src, dict):
            raise ValueError("model must be a JSON object")
        tol = _tol(tolerance)
        d = copy.deepcopy(src)
        new, report = fn(d, tol, **kw)
        if not report["changed"]:
            new = copy.deepcopy(src)
        if is_obj:
            return BuildingModel.from_dict(new), report
        return new, report
    return wrapper


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _length(m) -> float:
    return math.dist(m["pi"], m["pj"])


def _joint_coords(d: dict, with_records: bool = True) -> Dict[tuple, list]:
    """joint key -> representative coordinates (first occurrence) of every
    object end / corner (and point record)."""
    out: Dict[tuple, list] = {}

    def put(p):
        k = _key(p)
        if k not in out:
            out[k] = [float(v) for v in p]
    for m in d.get("members") or []:
        put(m["pi"]), put(m["pj"])
    for s in d.get("shells") or []:
        for c in s["corners"]:
            put(c)
    for lk in d.get("links") or []:
        put(lk["pi"]), put(lk["pj"])
    if with_records:
        for lst, _k in _ed._point_lists(d):
            for r in lst:
                if isinstance(r, dict) and "point" in r:
                    put(r["point"])
    return out


def _frame_link_keys(d: dict) -> Dict[tuple, int]:
    cnt: Dict[tuple, int] = {}
    for coll in ("members", "links"):
        for o in d.get(coll) or []:
            for e in ("pi", "pj"):
                k = _key(o[e])
                cnt[k] = cnt.get(k, 0) + 1
    return cnt


def _supported_keys(d: dict) -> set:
    out = {_key(r["point"]) for r in d.get("supports") or []}
    out |= {_key(r["point"]) for r in d.get("spring_supports") or []}
    return out


def _unsplittable(d: dict, m: dict) -> Optional[str]:
    """Why a member must NOT be divided (None = it may be)."""
    if str(m.get("axial_limit", "both")) != "both":
        return "axial-only (Truss) member"
    if m.get("curve"):
        return "curved member"
    sec = (d.get("sections") or {}).get(m.get("section"))
    if isinstance(sec, dict) and sec.get("kind", "prismatic") != "prismatic":
        return "nonprismatic section"
    h = m.get("hinges", "none")
    if isinstance(h, str) and h not in ("none", ""):
        return f"automatic hinges ({h})"
    return None


def _member_loads_count(d: dict, uid: str) -> int:
    n = 0
    for pat in (d.get("patterns") or {}).values():
        for k in ("member_loads", "member_udls", "thermal_loads"):
            n += sum(1 for x in pat.get(k) or [] if x.get("member_uid") == uid)
    return n


def _shell_loads_count(d: dict, uid: str) -> int:
    n = 0
    for pat in (d.get("patterns") or {}).values():
        for k in ("area_loads", "shell_thermal_loads"):
            n += sum(1 for x in pat.get(k) or [] if x.get("region_uid") == uid)
    return n


def _split_at(ctx: _ed._Ctx, uid: str, cuts: List[Tuple[float, list]],
              min_gap: float) -> List[str]:
    """Divide member ``uid`` at the fractions of ``cuts`` [(t, point)]; the
    piece ends at each cut are set to EXACTLY ``point`` (so they meet the
    joint / the crossing partner).  Loads, releases, hinges, offsets and
    references follow (:func:`skyframe.core.edit._split_member`)."""
    m = ctx.member(uid)
    L = _length(m)
    cuts = sorted((t, p) for t, p in cuts if 1e-9 < t < 1.0 - 1e-9)
    kept: List[Tuple[float, list]] = []
    for t, p in cuts:
        if (t * L <= min_gap or (1.0 - t) * L <= min_gap
                or (kept and (t - kept[-1][0]) * L <= min_gap)):
            continue
        kept.append((t, p))
    if not kept:
        return [uid]
    uids = _ed._split_member(ctx, uid, [t for t, _p in kept])
    if len(uids) != len(kept) + 1:          # defensive: should not happen
        return uids
    for k, (_t, p) in enumerate(kept):
        q = _clean(p)
        ctx.member(uids[k])["pj"] = list(q)
        ctx.member(uids[k + 1])["pi"] = list(q)
    return uids


def _fmt(p) -> str:
    return _ck._fmt(p)


# --------------------------------------------------------------------------- #
# fixes
# --------------------------------------------------------------------------- #
@_model_api
def fix_merge_joints(d: dict, tol: float) -> Tuple[dict, dict]:
    """Merge every cluster of joints closer than ``tol`` into one joint (a
    supported joint wins, else the joint with most objects, else the
    smallest coordinates) and remap every reference to it."""
    rep = _Rep("merge_joints")
    ctx = _ed._Ctx(d)
    owners: Dict[tuple, List[str]] = {}
    for m in d["members"]:
        for e in ("pi", "pj"):
            owners.setdefault(_key(m[e]), []).append(m["uid"])
    for s in d["shells"]:
        for c in s["corners"]:
            owners.setdefault(_key(c), []).append(s["uid"])
    for lk in d["links"]:
        for e in ("pi", "pj"):
            owners.setdefault(_key(lk[e]), []).append(lk["uid"])
    res = _ed.op_merge_joints(ctx, _ed._new_sel(), {"tolerance": tol})
    for c in res["merged"]:
        objs = [o for p in [c["to"]] + c["from"]
                for o in owners.get(tuple(p), [])]
        rep.add(f"Merged {len(c['from'])} joint(s) into {_fmt(c['to'])}",
                objs, c["to"], to=c["to"], merged=c["from"])
    # objects the merge collapsed (edit._cleanup_degenerate)
    for kind, label in (("members", "frame"), ("shells", "shell")):
        for u in ctx.deleted[kind]:
            rep.add(f"Deleted {label} {u} (zero-length, degenerate or "
                    "duplicate after the merge)", [u], None, removed=[u])
    return d, _fix_report("merge_joints", rep, ctx, bool(res["merged"]))


@_model_api
def fix_remove_zero_length(d: dict, tol: float) -> Tuple[dict, dict]:
    """Delete frames shorter than ``tol``; when the two ends are distinct
    joints they are merged first (so the frame's connection survives)."""
    rep = _Rep("remove_zero_length")
    ctx = _ed._Ctx(d)
    changed = False
    for _pass in range(MAX_PASSES):
        zero = [m for m in d["members"] if _length(m) < tol]
        if not zero:
            break
        m = zero[0]
        uid, L = m["uid"], _length(m)
        ki, kj = _key(m["pi"]), _key(m["pj"])
        nloads = _member_loads_count(d, uid)
        msg = f"Deleted zero-length frame {uid} ({L:.3g} m)"
        if ki != kj:
            sup = _supported_keys(d)
            cnt = ctx.object_joints()
            # same rule as Merge Joints: supported, then busiest, then min
            keep, drop = sorted((ki, kj), key=lambda q: (
                q not in sup, -cnt.get(q, 0), q))
            target = list(keep)
            touched = _ed._remap_points(
                ctx, lambda p: target if _key(p) == drop else None)
            for kind, u in touched:
                if not (kind == "members" and u == uid):
                    ctx.mark_modified(kind, u)
            _ed._dedupe_point_records(d)
            msg += f"; joint {_fmt(drop)} merged into {_fmt(keep)}"
        if nloads:
            msg += f"; {nloads} load(s) on it dropped"
            ctx.warnings.append(f"frame {uid}: {nloads} load(s) dropped "
                                "with the zero-length frame")
        _ed._delete_objects(ctx, members=[uid])
        rep.add(msg, [uid], m["pi"], loads_dropped=nloads)
        changed = True
    return d, _fix_report("remove_zero_length", rep, ctx, changed)


def _overlap_cuts(d: dict, tol: float) -> Dict[str, List[Tuple[float, list]]]:
    """Collinear overlapping frames: each is cut at the other's ends that
    lie strictly inside its span."""
    live = [m for m in d["members"] if _length(m) >= tol]
    cuts: Dict[str, List[Tuple[float, list]]] = {}
    if len(live) < 2:
        return cuts
    lens = sorted(_length(m) for m in live)
    grid = _ck._SpatialHash(max(lens[len(lens) // 2], 10.0 * tol))
    for idx, m in enumerate(live):
        lo = tuple(min(m["pi"][k], m["pj"][k]) - tol for k in range(3))
        hi = tuple(max(m["pi"][k], m["pj"][k]) + tol for k in range(3))
        grid.insert(idx, lo, hi)
    for a, b in sorted(grid.pairs()):
        A, B = live[a], live[b]
        pa, qa = tuple(map(float, A["pi"])), tuple(map(float, A["pj"]))
        pb, qb = tuple(map(float, B["pi"])), tuple(map(float, B["pj"]))
        da, db = _ck._sub(qa, pa), _ck._sub(qb, pb)
        La, Lb = _ck._norm(da), _ck._norm(db)
        if _ck._norm(_ck._cross(da, db)) / (La * Lb) >= 1e-6:
            continue                                  # not parallel
        if _ck._norm(_ck._cross(_ck._sub(pb, pa), da)) / La > tol:
            continue                                  # parallel, offset
        ua = _ck._scale(da, 1.0 / La)
        t0, t1 = sorted((_ck._dot(_ck._sub(pb, pa), ua),
                         _ck._dot(_ck._sub(qb, pa), ua)))
        if min(La, t1) - max(0.0, t0) <= tol:
            continue                                  # touching / apart
        for X, Y, LX in ((A, B, La), (B, A, Lb)):
            for e in ("pi", "pj"):
                dd, t = _ck._point_seg_dist(tuple(map(float, Y[e])),
                                            tuple(map(float, X["pi"])),
                                            tuple(map(float, X["pj"])))
                if dd <= tol and t * LX > tol and (1.0 - t) * LX > tol:
                    cuts.setdefault(X["uid"], []).append(
                        (t, [float(v) for v in Y[e]]))
    return cuts


@_model_api
def fix_remove_duplicates(d: dict, tol: float) -> Tuple[dict, dict]:
    """Remove frames that connect the same two joints and shells on the
    same corners (the copy carrying the most loads is kept, ties -> the
    first); collinear overlapping frames are first divided at each other's
    ends so their common part becomes an exact duplicate."""
    rep = _Rep("remove_duplicates")
    ctx = _ed._Ctx(d)
    changed = False
    for _pass in range(MAX_PASSES):
        did = False
        # 1. collinear overlaps -> split
        cuts = _overlap_cuts(d, tol)
        for uid in [m["uid"] for m in d["members"] if m["uid"] in cuts]:
            if _unsplittable(d, ctx.member(uid)):
                ctx.warnings.append(f"frame {uid}: overlapping but not "
                                    f"divided ({_unsplittable(d, ctx.member(uid))})")
                continue
            m = ctx.member(uid)
            pts = cuts[uid]
            uids = _split_at(ctx, uid, pts, tol)
            if len(uids) > 1:
                rep.add(f"Divided overlapping frame {uid} into "
                        f"{', '.join(uids)}", uids, m["pi"], pieces=uids)
                did = True
        # 2. exact duplicates (same two joints)
        groups: Dict[tuple, List[dict]] = {}
        for m in d["members"]:
            if _key(m["pi"]) == _key(m["pj"]):
                continue
            groups.setdefault(_ed._geom_key_member(m), []).append(m)
        for ms in groups.values():
            if len(ms) < 2:
                continue
            keep = max(ms, key=lambda m: (_member_loads_count(d, m["uid"]),
                                          -ms.index(m)))
            drop = [m["uid"] for m in ms if m is not keep]
            nl = sum(_member_loads_count(d, u) for u in drop)
            for u in drop:
                _ed._replace_uid_refs(d, "members", u, [keep["uid"]])
            _ed._delete_objects(ctx, members=drop)
            msg = (f"Deleted duplicate frame(s) {', '.join(drop)} "
                   f"(kept {keep['uid']})")
            if nl:
                msg += f"; {nl} load(s) on the deleted copies dropped"
                ctx.warnings.append(f"duplicate frame(s) {drop}: {nl} "
                                    "load(s) dropped")
            mid = _ed._lerp(keep["pi"], keep["pj"], 0.5)
            rep.add(msg, [keep["uid"]] + drop, mid, kept=keep["uid"],
                    removed=drop, loads_dropped=nl)
            did = True
        # 3. duplicate shells (same corner set)
        sgroups: Dict[tuple, List[dict]] = {}
        for s in d["shells"]:
            sgroups.setdefault(_ed._geom_key_shell(s), []).append(s)
        for ss in sgroups.values():
            if len(ss) < 2:
                continue
            keep = max(ss, key=lambda s: (_shell_loads_count(d, s["uid"]),
                                          -ss.index(s)))
            drop = [s["uid"] for s in ss if s is not keep]
            nl = sum(_shell_loads_count(d, u) for u in drop)
            for u in drop:
                _ed._replace_uid_refs(d, "shells", u, [keep["uid"]])
            _ed._delete_objects(ctx, shells=drop)
            msg = (f"Deleted duplicate shell(s) {', '.join(drop)} "
                   f"(kept {keep['uid']})")
            if nl:
                msg += f"; {nl} load(s) on the deleted copies dropped"
                ctx.warnings.append(f"duplicate shell(s) {drop}: {nl} "
                                    "load(s) dropped")
            rep.add(msg, [keep["uid"]] + drop, keep["corners"][0],
                    kept=keep["uid"], removed=drop, loads_dropped=nl)
            did = True
        if not did:
            break
        changed = True
    return d, _fix_report("remove_duplicates", rep, ctx, changed)


def _shell_ok(corners: List[list]) -> bool:
    """Would a shell with these corners pass BuildingModel validation?"""
    keys = [_key(c) for c in corners]
    if len(set(keys)) != len(keys):
        return False
    if len(corners) == 4:
        c = [tuple(map(float, p)) for p in corners]
        n = _ck._cross(_ck._sub(c[1], c[0]), _ck._sub(c[2], c[0]))
        nn = _ck._norm(n)
        if nn < 1e-12:
            return False
        if abs(_ck._dot(_ck._sub(c[3], c[0]), n)) / nn > _ck.PLANAR_TOL:
            return False
        if 0.5 * _ck._norm(_ck._newell_normal(c)) < 1e-9:
            return False
        g = _ck._shell_geometry(types.SimpleNamespace(corners=corners))
        p2 = g["pts2"]
        return not (_ck._seg2_intersect(p2[0], p2[1], p2[2], p2[3])
                    or _ck._seg2_intersect(p2[1], p2[2], p2[3], p2[0]))
    from skyframe.core.polymesh import polygon_problems
    return not polygon_problems(corners)


@_model_api
def fix_snap_shell_corners(d: dict, tol: float) -> Tuple[dict, dict]:
    """Move shell corners that are within ``tol`` of (but not on) a frame /
    link end onto it -- else onto another shell's corner -- so the shell
    meshes connect.  Frame / link ends never move.  A snap that would make
    a shell warped, self-intersecting or collapse two of its corners is
    skipped (warning)."""
    rep = _Rep("snap_shell_corners")
    ctx = _ed._Ctx(d)
    fl = _frame_link_keys(d)
    coords = _joint_coords(d, with_records=False)
    ckeys = sorted({_key(c) for s in d["shells"] for c in s["corners"]})
    movable = [k for k in ckeys if k not in fl]
    if not movable:
        return d, _fix_report("snap_shell_corners", rep, ctx, False)
    pts = sorted(set(movable) | set(fl))
    grid = _ck._SpatialHash(max(tol, 1e-9) * 2.0)
    for i, p in enumerate(pts):
        grid.insert(i, p, p)
    frozen: set = set()
    skipped: Dict[str, str] = {}
    mapping: Dict[tuple, tuple] = {}
    for _pass in range(MAX_PASSES):
        # clusters of movable corner keys (union-find within tol)
        uf = _ck._UF()
        live = [k for k in movable if k not in frozen]
        for k in live:
            uf.add(k)
        for k in live:
            lo = _ck._sub(k, (tol, tol, tol))
            hi = _ck._add(k, (tol, tol, tol))
            for j in grid.query(lo, hi):
                q = pts[j]
                if q != k and q in uf.parent and math.dist(k, q) <= tol:
                    uf.union(k, q)
        clusters: Dict[tuple, List[tuple]] = {}
        for k in live:
            clusters.setdefault(uf.find(k), []).append(k)

        def nearest_end(k):
            best = None
            lo = _ck._sub(k, (tol, tol, tol))
            hi = _ck._add(k, (tol, tol, tol))
            for j in grid.query(lo, hi):
                q = pts[j]
                if q in fl:
                    dd = math.dist(k, q)
                    if dd <= tol and (best is None or (dd, q) < best):
                        best = (dd, q)
            return best
        mapping = {}
        for members in clusters.values():
            own = {k: nearest_end(k) for k in members}
            cands = sorted(v for v in own.values() if v is not None)
            for k in members:
                if own[k] is not None:          # its own nearest frame end
                    mapping[k] = own[k][1]
                elif cands:                     # the cluster's frame end
                    mapping[k] = cands[0][1]
                elif min(members) != k:         # shell-only: lowest corner
                    mapping[k] = min(members)
        # validate the shells the mapping touches
        bad = []
        for s in d["shells"]:
            ks = [_key(c) for c in s["corners"]]
            if not any(k in mapping for k in ks):
                continue
            new = [list(coords[mapping[k]]) if k in mapping else list(c)
                   for k, c in zip(ks, s["corners"])]
            if not _shell_ok(new):
                bad.append(s["uid"])
                for k in ks:
                    if k in mapping or k in movable:
                        frozen.add(k)
        if not bad:
            break
        for u in bad:
            skipped[u] = "the snap would warp, fold or collapse it"
    for u in sorted(skipped):
        ctx.warnings.append(f"shell {u}: corners not snapped "
                            f"({skipped[u]})")
    if not mapping:
        return d, _fix_report("snap_shell_corners", rep, ctx, False)
    owners: Dict[tuple, List[str]] = {}
    for s in d["shells"]:
        for c in s["corners"]:
            owners.setdefault(_key(c), []).append(s["uid"])
    for coll in ("members", "links"):
        for o in d[coll]:
            for e in ("pi", "pj"):
                owners.setdefault(_key(o[e]), []).append(o["uid"])
    target = {k: list(coords[v]) for k, v in mapping.items()}
    touched = _ed._remap_points(ctx, lambda p: target.get(_key(p)))
    for kind, u in touched:
        ctx.mark_modified(kind, u)
    _ed._dedupe_point_records(d)
    for k in sorted(mapping):
        v = mapping[k]
        rep.add(f"Snapped shell corner {_fmt(k)} to "
                f"{'frame/link end' if v in fl else 'shell corner'} "
                f"{_fmt(v)} ({math.dist(k, v):.3g} m)",
                owners.get(k, []) + owners.get(v, []), v,
                **{"from": list(k), "to": list(v)})
    return d, _fix_report("snap_shell_corners", rep, ctx, True)


@_model_api
def fix_split_at_joints(d: dict, tol: float) -> Tuple[dict, dict]:
    """Divide every frame at the joints (object ends / corners and point
    records) lying within ``tol`` of its axis, strictly inside the span.
    Point loads go to the piece that holds them; distributed and
    trapezoidal loads are split by position (the trapezoid ordinates are
    interpolated); full-span legacy UDLs and temperature loads apply to
    every piece; moment releases stay at the outer ends."""
    rep = _Rep("split_at_joints")
    ctx = _ed._Ctx(d)
    changed = False
    warned: set = set()
    for _pass in range(MAX_PASSES):
        coords = _joint_coords(d)
        keys = sorted(coords)
        if not keys or not d["members"]:
            break
        lens = sorted(_length(m) for m in d["members"])
        grid = _ck._SpatialHash(max(lens[len(lens) // 2], 10.0 * tol))
        for i, k in enumerate(keys):
            grid.insert(i, k, k)
        plan: Dict[str, List[Tuple[float, list]]] = {}
        for m in d["members"]:
            L = _length(m)
            if L <= 2.0 * tol:
                continue
            a = tuple(map(float, m["pi"]))
            b = tuple(map(float, m["pj"]))
            lo = tuple(min(a[k], b[k]) - tol for k in range(3))
            hi = tuple(max(a[k], b[k]) + tol for k in range(3))
            hits = []
            for j in sorted(grid.query(lo, hi)):
                p = keys[j]
                dd, t = _ck._point_seg_dist(p, a, b)
                if dd <= tol and t * L > tol and (1.0 - t) * L > tol:
                    hits.append((t, coords[p]))
            if not hits:
                continue
            why = _unsplittable(d, m)
            if why:
                if m["uid"] not in warned:
                    warned.add(m["uid"])
                    ctx.warnings.append(f"frame {m['uid']}: joint(s) on its "
                                        f"span left unconnected ({why})")
                continue
            plan[m["uid"]] = hits
        done = False
        for uid in [m["uid"] for m in d["members"] if m["uid"] in plan]:
            m0 = copy.deepcopy(ctx.member(uid))
            uids = _split_at(ctx, uid, plan[uid], tol)
            if len(uids) > 1:
                at = [ctx.member(u)["pi"] for u in uids[1:]]
                rep.add(f"Divided frame {uid} at {len(at)} joint(s) into "
                        f"{', '.join(uids)}", uids,
                        _ed._lerp(m0["pi"], m0["pj"], 0.5), pieces=uids,
                        at=[list(p) for p in at])
                done = True
        if not done:
            break
        changed = True
    return d, _fix_report("split_at_joints", rep, ctx, changed)


@_model_api
def fix_split_at_crossings(d: dict, tol: float) -> Tuple[dict, dict]:
    """Divide BOTH frames of every crossing (closest approach within
    ``tol``, strictly inside both spans, not parallel) at the midpoint of
    the two closest points, so they share a joint there."""
    rep = _Rep("split_at_crossings")
    ctx = _ed._Ctx(d)
    changed = False
    warned: set = set()
    for _pass in range(MAX_PASSES):
        live = [m for m in d["members"] if _length(m) > 2.0 * tol]
        if len(live) < 2:
            break
        lens = sorted(_length(m) for m in live)
        grid = _ck._SpatialHash(max(lens[len(lens) // 2], 10.0 * tol))
        for idx, m in enumerate(live):
            lo = tuple(min(m["pi"][k], m["pj"][k]) - tol for k in range(3))
            hi = tuple(max(m["pi"][k], m["pj"][k]) + tol for k in range(3))
            grid.insert(idx, lo, hi)
        plan: Dict[str, List[Tuple[float, list]]] = {}
        pairs = []
        for a, b in sorted(grid.pairs()):
            A, B = live[a], live[b]
            pa, qa = tuple(map(float, A["pi"])), tuple(map(float, A["pj"]))
            pb, qb = tuple(map(float, B["pi"])), tuple(map(float, B["pj"]))
            if any(min(pa[k], qa[k]) - tol > max(pb[k], qb[k])
                   or min(pb[k], qb[k]) - tol > max(pa[k], qa[k])
                   for k in range(3)):
                continue
            da, db = _ck._sub(qa, pa), _ck._sub(qb, pb)
            La, Lb = _ck._norm(da), _ck._norm(db)
            if _ck._norm(_ck._cross(da, db)) / (La * Lb) < 1e-6:
                continue                              # parallel
            s, t, c1, c2 = _ck._seg_closest(pa, qa, pb, qb)
            if math.dist(c1, c2) > tol:
                continue
            if not (s * La > tol and (1 - s) * La > tol
                    and t * Lb > tol and (1 - t) * Lb > tol):
                continue                              # T / shared end
            why = _unsplittable(d, A) or _unsplittable(d, B)
            if why:
                key = (A["uid"], B["uid"])
                if key not in warned:
                    warned.add(key)
                    ctx.warnings.append(f"frames {A['uid']} / {B['uid']}: "
                                        f"crossing left unconnected ({why})")
                continue
            X = _clean(_ck._scale(_ck._add(c1, c2), 0.5))
            plan.setdefault(A["uid"], []).append((s, X))
            plan.setdefault(B["uid"], []).append((t, X))
            pairs.append((A["uid"], B["uid"], X))
        if not plan:
            break
        for uid in [m["uid"] for m in d["members"] if m["uid"] in plan]:
            _split_at(ctx, uid, plan[uid], tol)
        for ua, ub, X in pairs:
            rep.add(f"Divided crossing frames {ua} and {ub} at {_fmt(X)}",
                    [ua, ub], X, at=list(X))
        changed = True
    return d, _fix_report("split_at_crossings", rep, ctx, changed)


_RECORD_LABEL = {"supports": "support", "spring_supports": "spring support",
                 "nodal_masses": "nodal mass",
                 "joint_diaphragms": "joint diaphragm",
                 "joint_panel_zones": "joint panel zone",
                 "nodal_loads": "nodal load",
                 "ground_displacements": "ground displacement",
                 "joint_temperatures": "joint temperature"}


@_model_api
def fix_remove_unconnected_joints(d: dict, tol: float) -> Tuple[dict, dict]:
    """Delete the point records (supports, springs, nodal masses, joint
    diaphragms / panel zones, nodal loads, ground displacements, joint
    temperatures, group points) whose point is not a node of the analysis
    mesh (object ends / corners, shell mesh nodes, frame auto-mesh
    points): they act on an isolated joint."""
    rep = _Rep("remove_unconnected_joints")
    ctx = _ed._Ctx(d)
    live = set(_joint_coords(d, with_records=False))
    pending = set()
    for lst, _k in _ed._point_lists(d):
        for r in lst:
            if isinstance(r, dict) and _key(r["point"]) not in live:
                pending.add(_key(r["point"]))
    for g in (d.get("groups") or {}).values():
        for p in g.get("points") or []:
            if _key(p) not in live:
                pending.add(_key(p))
    if not pending:
        return d, _fix_report("remove_unconnected_joints", rep, ctx, False)
    try:                                # shell mesh / auto-mesh nodes
        from skyframe.core.mesh import mesh_model
        mdl = _ck.load_model_lenient(copy.deepcopy(d))
        live |= {_key(p) for p in mesh_model(mdl).points}
    except Exception:                   # invalid model: object joints only
        pass
    dead = sorted(k for k in pending if k not in live)
    if not dead:
        return d, _fix_report("remove_unconnected_joints", rep, ctx, False)
    deadset = set(dead)
    what: Dict[tuple, List[str]] = {k: [] for k in dead}
    for lst, kind in _ed._point_lists(d):
        for r in lst:
            k = _key(r["point"])
            if k in deadset:
                what[k].append(_RECORD_LABEL.get(kind, kind))
    for name, g in (d.get("groups") or {}).items():
        for p in g.get("points") or []:
            if _key(p) in deadset:
                what[_key(p)].append(f"group {name} point")
    _ed._delete_point_records(ctx, deadset)
    for k in dead:
        ctx.deleted["points"].append(list(k))
        recs = what[k]
        counts: Dict[str, int] = {}
        for x in recs:
            counts[x] = counts.get(x, 0) + 1
        desc = ", ".join(f"{n} {lbl}{'s' if n > 1 else ''}"
                         for lbl, n in counts.items())
        rep.add(f"Removed unconnected joint {_fmt(k)} ({desc})", [], k,
                records=recs)
        if any(x in ("nodal load", "ground displacement",
                     "joint temperature") for x in recs):
            ctx.warnings.append(f"joint {_fmt(k)}: loads on the "
                                "unconnected joint removed")
    return d, _fix_report("remove_unconnected_joints", rep, ctx, True)


def report_mechanisms(model, tolerance: float = DEFAULT_TOLERANCE,
                      max_dofs: int = _ck.STABILITY_MAX_DOFS
                      ) -> Tuple[dict, dict]:
    """REPORT ONLY (the model is returned unchanged): the near-zero
    stiffness mechanisms of :func:`checks.stability_diagnostics` and the
    connectivity errors of :func:`checks.check_model`."""
    from skyframe.core.model import BuildingModel
    is_obj = isinstance(model, BuildingModel)
    src = model.to_dict() if is_obj else model
    tol = _tol(tolerance)
    rep = _Rep("report_mechanisms")
    stab = None
    warnings: List[str] = []
    try:
        mdl = _ck.load_model_lenient(copy.deepcopy(src))
        chk = _ck.check_model(mdl, tol)
        for iss in chk["issues"]:
            if iss["code"] in _INSTABILITY_CODES:
                rep.add(iss["message"], iss["objects"], iss["location"],
                        code=iss["code"], severity=iss["severity"])
    except Exception as exc:          # pragma: no cover - defensive
        warnings.append(f"check model failed: {exc}")
    try:
        full = _ck.stability_diagnostics(
            BuildingModel.from_dict(copy.deepcopy(src)), max_dofs=max_dofs)
        stab = {k: full.get(k) for k in (
            "stable", "built", "skipped", "n_equations", "n_mechanisms",
            "mechanisms", "unstable_dofs", "message", "issues")}
        for iss in full["issues"]:
            rep.add(iss["message"], iss["objects"], iss["location"],
                    code=iss["code"], severity=iss["severity"])
    except Exception as exc:
        warnings.append(f"stability diagnostics unavailable: {exc}")
    out = _fix_report("report_mechanisms", rep, None, False,
                      {"stability": stab})
    out["warnings"] = warnings
    return (model if is_obj else copy.deepcopy(src)), out


FIX_FUNCS: Dict[str, Callable] = {
    "merge_joints": fix_merge_joints,
    "remove_zero_length": fix_remove_zero_length,
    "remove_duplicates": fix_remove_duplicates,
    "snap_shell_corners": fix_snap_shell_corners,
    "split_at_joints": fix_split_at_joints,
    "split_at_crossings": fix_split_at_crossings,
    "remove_unconnected_joints": fix_remove_unconnected_joints,
    "report_mechanisms": report_mechanisms,
}


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #
def normalize_fixes(fixes) -> List[str]:
    """Validate a fix list; ``None`` -> DEFAULT_FIXES, ``"all"`` -> every
    fix; returned in the canonical order."""
    if fixes is None:
        return list(DEFAULT_FIXES)
    if fixes == "all":
        return list(REPAIR_FIXES)
    if not isinstance(fixes, list):
        raise ValueError("'fixes' must be a list of fix ids")
    want = set()
    for f in fixes:
        if f == "all":
            want.update(REPAIR_FIXES)
        elif f not in REPAIR_FIXES:
            raise ValueError(f"unknown fix {f!r} (one of "
                             f"{list(REPAIR_FIXES)})")
        else:
            want.add(f)
    return [f for f in REPAIR_FIXES if f in want]


def _check_summary(d: dict, tol: float) -> Optional[dict]:
    try:
        s = _ck.check_model(_ck.load_model_lenient(copy.deepcopy(d)),
                            tol)["summary"]
    except Exception:
        return None
    return {k: s[k] for k in ("errors", "warnings", "info", "by_code", "ok")}


def repair(model, fixes=None, tolerance: float = DEFAULT_TOLERANCE, *,
           check: bool = True,
           max_dofs: int = _ck.STABILITY_MAX_DOFS) -> Tuple[dict, dict]:
    """Apply ``fixes`` (canonical order) to a COPY of ``model`` (dict or
    BuildingModel); returns ``(new_model, report)``.  Raises ValueError on
    bad input.  ``check`` adds the Check Model summaries before / after."""
    from skyframe.core.model import BuildingModel
    is_obj = isinstance(model, BuildingModel)
    src = model.to_dict() if is_obj else model
    if not isinstance(src, dict):
        raise ValueError("model must be a JSON object")
    tol = _tol(tolerance)
    order = normalize_fixes(fixes)
    cur = src
    per: List[dict] = []
    for f in order:
        if f == "report_mechanisms":
            _m, r = report_mechanisms(cur, tol, max_dofs=max_dofs)
        else:
            nxt, r = FIX_FUNCS[f](cur, tol)
            if r["changed"]:
                cur = nxt
        per.append(r)
    changed = cur is not src
    new = copy.deepcopy(cur)
    agg_c, agg_d = _empty_lists(), _empty_lists()
    agg_m = {"members": [], "shells": [], "links": []}
    for r in per:
        for k in agg_c:
            agg_c[k] += [x for x in r["created"].get(k, [])]
            agg_d[k] += [x for x in r["deleted"].get(k, [])]
        for k in agg_m:
            agg_m[k] += [x for x in r["modified"].get(k, [])]
    # net effect: an object created then deleted within the run vanishes
    for k in ("members", "shells", "links"):
        both = set(agg_c[k]) & set(agg_d[k])
        agg_c[k] = [u for u in dict.fromkeys(agg_c[k]) if u not in both]
        agg_d[k] = [u for u in dict.fromkeys(agg_d[k]) if u not in both]
        gone = set(agg_d[k]) | set(agg_c[k])
        agg_m[k] = [u for u in dict.fromkeys(agg_m[k]) if u not in gone]
    report = {
        "changed": changed,
        "tolerance_m": tol,
        "fixes": order,
        "results": per,
        "actions": [a for r in per for a in r["actions"]],
        "warnings": [w for r in per for w in r["warnings"]],
        "counts": {r["fix"]: r["count"] for r in per},
        "created": agg_c, "deleted": agg_d, "modified": agg_m,
        "stability": next((r.get("stability") for r in per
                           if r["fix"] == "report_mechanisms"), None),
    }
    if check:
        report["check_before"] = _check_summary(src, tol)
        report["check_after"] = (report["check_before"] if not changed
                                 else _check_summary(new, tol))
    if is_obj:
        return BuildingModel.from_dict(new), report
    return new, report


def repair_json(model_dict: dict, fixes=None,
                tolerance: float = DEFAULT_TOLERANCE, **kw) -> Tuple[dict, dict]:
    """:func:`repair` + validation: the returned dict is
    ``BuildingModel.from_dict(new).to_dict()`` (the API echo)."""
    from skyframe.core.model import BuildingModel
    base = BuildingModel.from_dict(model_dict)
    new, report = repair(base.to_dict(), fixes, tolerance, **kw)
    return BuildingModel.from_dict(new).to_dict(), report

