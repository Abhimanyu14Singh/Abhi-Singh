"""ETABS-style Groups + user-defined staged-construction stage validation.

A GROUP is a named selection of model objects (ETABS Define > Groups):

    model.groups = {name: {"members": [uid, ...], "shells": [uid, ...],
                           "links": [uid, ...], "points": [[x, y, z], ...],
                           "color": str}}

Groups are pure bookkeeping — they never change an analysis by
themselves.  They are consumed by

* section cuts (``SectionCut.group``): the cut integrates only the group's
  objects ("section cut defined by group");
* :func:`group_end_resultant`: base-/story-like resultant of a group's
  member end forces at their low ends along an axis;
* user-defined staged construction (``StagedCase.stages`` as a LIST of
  stages whose operations ``add`` / ``remove`` / ``load`` a group; the
  engine side lives in :mod:`skyframe.engine.staged_user`).

Model-level consistency helpers (the UI calls them after editing):
:func:`prune_groups` (drop references to deleted objects),
:func:`rename_object_in_groups`, :func:`rename_group`, :func:`delete_group`.

See CONTRACT "Groups and user-defined staged construction".
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

GROUP_KINDS = ("members", "shells", "links")
GROUP_KEYS = GROUP_KINDS + ("points", "color")
STAGE_OPS = ("add", "remove", "load")
_STAGE_KEYS = ("name", "duration_days", "operations")
_OP_KEYS = {"add": ("op", "group", "age_days"),
            "remove": ("op", "group"),
            "load": ("op", "group", "pattern", "scale")}
#: default concrete age (days) of an object at the moment it is ADDED
DEFAULT_ADD_AGE_DAYS = 28.0
_PT_TOL = 1e-6


# --------------------------------------------------------------------------- #
# group normalisation / validation
# --------------------------------------------------------------------------- #
def normalize_group(g: Optional[dict]) -> dict:
    """Canonical group dict (every key present, lists copied)."""
    g = dict(g or {})
    unknown = [k for k in g if k not in GROUP_KEYS]
    if unknown:
        raise ValueError(f"group: unknown key(s) {unknown} (allowed: "
                         f"{GROUP_KEYS})")
    out = {}
    for k in GROUP_KINDS:
        v = g.get(k) or []
        if not isinstance(v, (list, tuple)):
            raise ValueError(f"group: {k!r} must be a list of uids")
        out[k] = [str(u) for u in v]
    pts = g.get("points") or []
    if not isinstance(pts, (list, tuple)):
        raise ValueError("group: 'points' must be a list of [x, y, z]")
    out["points"] = []
    for p in pts:
        if (not isinstance(p, (list, tuple)) or len(p) != 3
                or not all(isinstance(c, (int, float))
                           and not isinstance(c, bool)
                           and math.isfinite(c) for c in p)):
            raise ValueError("group: every point must be a finite "
                             "[x, y, z]")
        out["points"].append([float(c) for c in p])
    color = g.get("color", "")
    if not isinstance(color, str):
        raise ValueError("group: 'color' must be a string")
    out["color"] = color
    return out


def validate_group(model, name: str, g: dict) -> None:
    """Raise ValueError when a group is malformed or references unknown
    objects (duplicates inside one list are rejected too)."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("group: name must be a non-empty string")
    try:
        ng = normalize_group(g)
    except ValueError as exc:
        raise ValueError(f"Group {name}: {exc}") from None
    known = {"members": {m.uid for m in model.members},
             "shells": {r.uid for r in model.shells},
             "links": {lk.uid for lk in model.links}}
    for k in GROUP_KINDS:
        seen = set()
        for uid in ng[k]:
            if uid in seen:
                raise ValueError(f"Group {name}: duplicate {k[:-1]} "
                                 f"{uid!r}")
            seen.add(uid)
            if uid not in known[k]:
                raise ValueError(f"Group {name}: unknown {k[:-1]} {uid!r}")


def validate_groups(model) -> None:
    groups = getattr(model, "groups", None) or {}
    if not isinstance(groups, dict):
        raise ValueError("groups must be a dict name -> group")
    for name, g in groups.items():
        validate_group(model, name, g)


# --------------------------------------------------------------------------- #
# resolution + consistency helpers
# --------------------------------------------------------------------------- #
def _group(model, name: str) -> dict:
    groups = getattr(model, "groups", None) or {}
    if name not in groups:
        raise ValueError(f"Unknown group {name!r}")
    return normalize_group(groups[name])


def resolve_group(model, name: str) -> dict:
    """The group's objects: ``{"members": [FrameMember], "shells":
    [ShellRegion], "links": [LinkMember], "points": [(x, y, z)]}`` in
    model order (dangling references are skipped)."""
    g = _group(model, name)
    mem, sh, lk = set(g["members"]), set(g["shells"]), set(g["links"])
    return {"members": [m for m in model.members if m.uid in mem],
            "shells": [r for r in model.shells if r.uid in sh],
            "links": [x for x in model.links if x.uid in lk],
            "points": [tuple(p) for p in g["points"]]}


def groups_of(model, kind: str, uid: str) -> List[str]:
    """Names of every group that contains object ``uid`` of ``kind``."""
    if kind not in GROUP_KINDS:
        raise ValueError(f"kind must be one of {GROUP_KINDS}")
    return [n for n, g in (getattr(model, "groups", None) or {}).items()
            if uid in (g.get(kind) or [])]


def add_group(model, name: str, members: Sequence[str] = (),
              shells: Sequence[str] = (), links: Sequence[str] = (),
              points: Sequence[Sequence[float]] = (),
              color: str = "") -> dict:
    """Create or replace a group (validated)."""
    g = normalize_group({"members": list(members), "shells": list(shells),
                         "links": list(links),
                         "points": [list(p) for p in points],
                         "color": color})
    validate_group(model, name, g)
    model.groups[name] = g
    return g


def _group_references(model, name: str) -> List[str]:
    refs = []
    for cut in model.section_cuts:
        if getattr(cut, "group", None) == name:
            refs.append(f"section cut {cut.name!r}")
    for sc in model.staged_cases.values():
        if isinstance(sc.stages, list):
            for st in sc.stages:
                for op in st.get("operations") or []:
                    if op.get("group") == name:
                        refs.append(f"staged case {sc.name!r}")
                        break
    return sorted(set(refs))


def delete_group(model, name: str, force: bool = False) -> None:
    """Delete a group.  Refused (ValueError) while a section cut or a
    staged case references it, unless ``force`` (then the referencing
    section cuts lose their group filter and the staged operations naming
    the group are dropped)."""
    if name not in model.groups:
        raise ValueError(f"Unknown group {name!r}")
    refs = _group_references(model, name)
    if refs and not force:
        raise ValueError(f"Group {name!r} is referenced by "
                         f"{', '.join(refs)}")
    for cut in model.section_cuts:
        if getattr(cut, "group", None) == name:
            cut.group = None
    for sc in model.staged_cases.values():
        if isinstance(sc.stages, list):
            for st in sc.stages:
                st["operations"] = [op for op in st.get("operations") or []
                                    if op.get("group") != name]
    del model.groups[name]


def rename_group(model, old: str, new: str) -> None:
    """Rename a group and every section-cut / staged-operation reference."""
    if old not in model.groups:
        raise ValueError(f"Unknown group {old!r}")
    if not isinstance(new, str) or not new.strip():
        raise ValueError("group: new name must be a non-empty string")
    if new != old and new in model.groups:
        raise ValueError(f"Group {new!r} already exists")
    model.groups = {(new if k == old else k): v
                    for k, v in model.groups.items()}
    for cut in model.section_cuts:
        if getattr(cut, "group", None) == old:
            cut.group = new
    for sc in model.staged_cases.values():
        if isinstance(sc.stages, list):
            for st in sc.stages:
                for op in st.get("operations") or []:
                    if op.get("group") == old:
                        op["group"] = new


def prune_groups(model) -> Dict[str, Dict[str, List[str]]]:
    """Drop group references to objects that no longer exist (call after
    deleting members/shells/links).  Returns ``{group: {kind: [removed
    uids]}}`` for every group that changed."""
    known = {"members": {m.uid for m in model.members},
             "shells": {r.uid for r in model.shells},
             "links": {lk.uid for lk in model.links}}
    changed: Dict[str, Dict[str, List[str]]] = {}
    for name, g in model.groups.items():
        for k in GROUP_KINDS:
            lst = g.get(k) or []
            gone = [u for u in lst if u not in known[k]]
            if gone:
                g[k] = [u for u in lst if u in known[k]]
                changed.setdefault(name, {})[k] = gone
    return changed


def remove_object_from_groups(model, kind: str, uid: str) -> List[str]:
    """Remove one object from every group; returns the affected groups."""
    if kind not in GROUP_KINDS:
        raise ValueError(f"kind must be one of {GROUP_KINDS}")
    hit = []
    for name, g in model.groups.items():
        if uid in (g.get(kind) or []):
            g[kind] = [u for u in g[kind] if u != uid]
            hit.append(name)
    return hit


def rename_object_in_groups(model, kind: str, old: str, new: str
                            ) -> List[str]:
    """Rename an object uid inside every group; returns affected groups."""
    if kind not in GROUP_KINDS:
        raise ValueError(f"kind must be one of {GROUP_KINDS}")
    hit = []
    for name, g in model.groups.items():
        lst = g.get(kind) or []
        if old in lst:
            g[kind] = [new if u == old else u for u in lst]
            hit.append(name)
    return hit


def move_point_in_groups(model, old: Sequence[float],
                         new: Optional[Sequence[float]]) -> List[str]:
    """Move (``new`` = [x,y,z]) or delete (``new`` = None) a group point."""
    hit = []
    for name, g in model.groups.items():
        pts = g.get("points") or []
        out, changed = [], False
        for p in pts:
            if all(abs(float(p[i]) - float(old[i])) < _PT_TOL
                   for i in range(3)):
                changed = True
                if new is not None:
                    out.append([float(c) for c in new])
            else:
                out.append(p)
        if changed:
            g["points"] = out
            hit.append(name)
    return hit


# --------------------------------------------------------------------------- #
# user-defined stage validation
# --------------------------------------------------------------------------- #
def _finite(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v))


def normalize_stages(stages) -> List[dict]:
    """Deep-copied stage list with defaults filled in (duration 0, add
    age 28 d, load scale 1)."""
    out = []
    for st in stages:
        ops_out = []
        for op in st.get("operations") or []:
            o = dict(op)
            if o.get("op") == "add":
                o.setdefault("age_days", DEFAULT_ADD_AGE_DAYS)
            if o.get("op") == "load":
                o.setdefault("scale", 1.0)
            ops_out.append(o)
        out.append({"name": str(st.get("name")),
                    "duration_days": float(st.get("duration_days", 0.0)),
                    "operations": ops_out})
    return out


def validate_user_stages(model, sc) -> None:
    """Validate ``StagedCase.stages`` given as a list of stages, including
    the activation sequence (an ``add`` may only name inactive objects, a
    ``remove`` only active ones)."""
    nm = sc.name
    stages = sc.stages
    if not isinstance(stages, list) or not stages:
        raise ValueError(f"Staged case {nm}: stages must be 'per_story' or "
                         "a non-empty list of stages")
    groups = getattr(model, "groups", None) or {}
    names = set()
    active = {k: set() for k in GROUP_KINDS}
    for i, st in enumerate(stages):
        if not isinstance(st, dict):
            raise ValueError(f"Staged case {nm}: stage {i} must be a dict")
        for k in st:
            if k not in _STAGE_KEYS:
                raise ValueError(f"Staged case {nm}: stage {i}: unknown "
                                 f"key {k!r} (allowed: {_STAGE_KEYS})")
        sname = st.get("name")
        if not isinstance(sname, str) or not sname.strip():
            raise ValueError(f"Staged case {nm}: stage {i} needs a name")
        if sname in names:
            raise ValueError(f"Staged case {nm}: duplicate stage name "
                             f"{sname!r}")
        names.add(sname)
        dur = st.get("duration_days", 0.0)
        if not (_finite(dur) and dur >= 0.0):
            raise ValueError(f"Staged case {nm}: stage {sname}: "
                             "duration_days must be finite and >= 0")
        ops = st.get("operations") or []
        if not isinstance(ops, list):
            raise ValueError(f"Staged case {nm}: stage {sname}: operations "
                             "must be a list")
        for op in ops:
            if not isinstance(op, dict) or op.get("op") not in STAGE_OPS:
                raise ValueError(f"Staged case {nm}: stage {sname}: every "
                                 f"operation needs op in {STAGE_OPS}")
            kind = op["op"]
            for k in op:
                if k not in _OP_KEYS[kind]:
                    raise ValueError(f"Staged case {nm}: stage {sname}: "
                                     f"{kind} operation: unknown key {k!r}")
            gname = op.get("group")
            if gname not in groups:
                raise ValueError(f"Staged case {nm}: stage {sname}: unknown "
                                 f"group {gname!r}")
            g = normalize_group(groups[gname])
            if kind == "add":
                age = op.get("age_days", DEFAULT_ADD_AGE_DAYS)
                if not (_finite(age) and age >= 0.0):
                    raise ValueError(f"Staged case {nm}: stage {sname}: "
                                     "age_days must be finite and >= 0")
                for k in GROUP_KINDS:
                    dup = [u for u in g[k] if u in active[k]]
                    if dup:
                        raise ValueError(
                            f"Staged case {nm}: stage {sname}: add {gname}: "
                            f"{k} {dup} already active")
                    active[k].update(g[k])
            elif kind == "remove":
                for k in GROUP_KINDS:
                    miss = [u for u in g[k] if u not in active[k]]
                    if miss:
                        raise ValueError(
                            f"Staged case {nm}: stage {sname}: remove "
                            f"{gname}: {k} {miss} not active")
                    active[k].difference_update(g[k])
            else:
                pat = op.get("pattern")
                if pat not in model.patterns:
                    raise ValueError(f"Staged case {nm}: stage {sname}: "
                                     f"unknown pattern {pat!r}")
                if not _finite(op.get("scale", 1.0)):
                    raise ValueError(f"Staged case {nm}: stage {sname}: "
                                     "scale must be finite")


# --------------------------------------------------------------------------- #
# group-based output
# --------------------------------------------------------------------------- #
def group_end_resultant(model, case, group: str, axis: str = "z",
                        about: Optional[Sequence[float]] = None) -> dict:
    """Base-/story-like resultant of a group's member END forces.

    For every member of the group that spans a finite length along
    ``axis``, the internal force at its LOW end (smaller ``axis``
    coordinate — e.g. the bottom of a column for ``axis="z"``) is taken
    from the member stations, rotated to global axes and oriented exactly
    like a :class:`~skyframe.core.model.SectionCut` (the force the
    material on the negative side exerts on the positive side: a gravity
    column group reports a positive ``FZ`` equal to the weight carried).
    Moments are about ``about`` (default: the centroid of the low ends).
    Equals a group section cut taken just above the low ends.
    """
    from skyframe.engine.opensees_engine import (_SECTION_AXIS_INDEX,
                                                 _local_axes)
    ai = _SECTION_AXIS_INDEX[axis]
    contribs = []
    for m in resolve_group(model, group)["members"]:
        ci, cj = m.pi[ai], m.pj[ai]
        if abs(cj - ci) <= 1e-9:
            continue
        st = case.member_stations.get(m.uid)
        if not st or not st.get("x"):
            continue
        idx = 0 if ci < cj else -1
        low = m.pi if ci < cj else m.pj
        xax, yax, zax, _, _ = _local_axes(m)
        N, V2, V3 = st["N"][idx], st["V2"][idx], st["V3"][idx]
        T, M2, M3 = st["T"][idx], st["M2"][idx], st["M3"][idx]
        sign = -1.0 if (cj - ci) > 0 else 1.0
        F = [sign * (N * xax[k] + V2 * yax[k] + V3 * zax[k])
             for k in range(3)]
        Mv = [sign * (T * xax[k] + M2 * yax[k] + M3 * zax[k])
              for k in range(3)]
        contribs.append((tuple(low), F, Mv))
    if about is None:
        about = (tuple(sum(c[0][k] for c in contribs) / len(contribs)
                       for k in range(3)) if contribs else (0.0, 0.0, 0.0))
    out = {k: 0.0 for k in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
    for r, F, Mv in contribs:
        d = [r[k] - float(about[k]) for k in range(3)]
        out["FX"] += F[0]
        out["FY"] += F[1]
        out["FZ"] += F[2]
        out["MX"] += Mv[0] + d[1] * F[2] - d[2] * F[1]
        out["MY"] += Mv[1] + d[2] * F[0] - d[0] * F[2]
        out["MZ"] += Mv[2] + d[0] * F[1] - d[1] * F[0]
    out["n_members"] = len(contribs)
    out["about"] = [float(v) for v in about]
    return out
