"""Multiple towers — follow-ups (CONTRACT "Multiple towers — follow-ups").

Model-side helpers of the C9 follow-ups; everything is inert without
``model.towers`` (and without the new optional fields), so legacy models
stay byte-identical.

* ``control_tower`` on :class:`~skyframe.core.model.PushoverCase` and
  :class:`~skyframe.core.nonlinear_static.NonlinearStaticCase`: the
  monitored joint (and, for the default ``roof_point`` push, the pushed
  roof) is the TOP story of that tower (:func:`validate`).
* Named diaphragms x towers: a named diaphragm is SPLIT per tower — on
  every (tower, story) the assignments that belong to that tower form
  their own diaphragm, so one name never ties two towers together
  (:func:`named_story_names`, used by the engine).
* Staged construction across towers (``per_story``): stages follow the
  tower-stories in ascending elevation (ties -> tower order, then model
  order) — :func:`stage_order`; the stage sub-models carry the towers
  (:func:`carry`).
* Model tables: the Story Definitions ``tower`` column
  (:func:`story_tower_get` / :func:`story_tower_put`,
  :func:`sync_tower_story_lists`).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from . import towers as _ct

_TOL = 1e-6


# ------------------------------------------------------------ validation
def _check_control_tower(model, nm: str, case) -> None:
    ct = getattr(case, "control_tower", None)
    if ct is None:
        return
    if not isinstance(ct, str) or not ct:
        raise ValueError(f"{nm}: control_tower must be a tower name "
                         "(or null)")
    if not _ct.has_towers(model):
        raise ValueError(f"{nm}: control_tower {ct!r} needs model.towers "
                         "(Define > Towers); none are defined")
    if ct not in _ct.tower_names(model):
        raise ValueError(f"{nm}: control_tower references unknown tower "
                         f"{ct!r}")
    if (getattr(case, "control_story", None) is not None
            or getattr(case, "control_point", None) is not None):
        raise ValueError(f"{nm}: give control_tower OR control_story / "
                         "control_point, not both")


def validate(model) -> None:
    """``control_tower`` of pushover and nonlinear static cases."""
    for po in (getattr(model, "pushover_cases", None) or {}).values():
        _check_control_tower(model, f"Pushover case {po.name}", po)
    for c in (getattr(model, "nonlinear_static_cases", None) or {}).values():
        _check_control_tower(model, f"Nonlinear static case {c.name}", c)


def control_story(model, tower: str):
    """The top story (highest elevation) of ``tower``."""
    sts = _ct.tower_stories(model, tower)
    return max(sts, key=lambda s: s.elevation)


# --------------------------------------------------- named diaphragms
def named_story_names(model) -> List[str]:
    """Named stories with towers: a story is named only when a joint /
    shell diaphragm assignment at its elevation belongs to ITS tower
    (joint: :func:`towers.point_tower`; shell: :func:`towers.object_tower`).
    The engine partitions each story over its own tower's nodes only, so
    a diaphragm name used in two towers becomes one diaphragm per tower."""
    ctx = _ct.context(model)
    st = ctx.st_tower
    out = []
    for s in model.stories:
        tw = st[s.name]
        hit = any(abs(float(e["point"][2]) - s.elevation) < _TOL
                  and ctx.point(e["point"]) == tw
                  for e in model.joint_diaphragms)
        if not hit:
            hit = any(getattr(r, "diaphragm", "")
                      and abs(max(c[2] for c in r.corners) - s.elevation)
                      < _TOL and ctx.obj(r) == tw for r in model.shells)
        if hit:
            out.append(s.name)
    return out


def diaphragm_towers(model) -> Dict[str, List[str]]:
    """{diaphragm name: [towers its assignments belong to]} (tower
    order) — a name listing several towers is split, one diaphragm per
    tower and story (results report them under each tower's story)."""
    if not _ct.has_towers(model):
        return {}
    ctx = _ct.context(model)
    acc: Dict[str, set] = {}
    for e in getattr(model, "joint_diaphragms", None) or []:
        acc.setdefault(e["diaphragm"], set()).add(ctx.point(e["point"]))
    for r in model.shells:
        d = getattr(r, "diaphragm", "")
        if d:
            acc.setdefault(d, set()).add(ctx.obj(r))
    return {d: [t for t in ctx.names if t in ts] for d, ts in acc.items()}


# --------------------------------------------------- staged construction
def stage_order(model) -> List[str]:
    """Story names in per_story construction order with towers: ascending
    elevation, ties broken by tower order, then model order."""
    names = _ct.tower_names(model)
    st = _ct.story_tower_map(model)
    idx = {s.name: i for i, s in enumerate(model.stories)}
    return [s.name for s in sorted(
        model.stories, key=lambda s: (round(s.elevation, 6),
                                      names.index(st[s.name]),
                                      idx[s.name]))]


def tower_story_of_z(model, tower: str, z: float):
    """The story of ``tower`` owning elevation ``z`` (story covers
    (elev_below, elev]); at/below its first story -> the first; above its
    roof -> its top story."""
    sts = _ct.tower_stories(model, tower)
    for s in sts:
        if z <= s.elevation + _TOL:
            return s
    return sts[-1]


def carry(model, sub, keys: Optional[Iterable] = None) -> None:
    """Copy the tower definition onto a staged sub-model ``sub`` (no-op
    without towers).  Towers without a story in ``sub`` are dropped; with
    ``keys`` (active point keys, user-defined staging) a story is kept
    only when an active point at its elevation belongs to its tower."""
    if not _ct.has_towers(model):
        return
    if keys is not None:
        ctx = _ct.context(model)
        keys = list(keys)
        sub.stories = [s for s in sub.stories if any(
            abs(k[2] - s.elevation) < _TOL
            and ctx.point(k) == ctx.st_tower[s.name] for k in keys)]
    st = _ct.story_tower_map(model)
    present = {st[s.name] for s in sub.stories}
    sub.towers = [dict(t) for t in model.towers if t["name"] in present]
    sub.grid_systems = list(model.grid_systems)
    sub.grid = model.grid
    sub.joint_towers = [dict(j) for j in model.joint_towers
                        if j["tower"] in present]
    if sub.towers and sub.towers[0]["name"] != model.towers[0]["name"]:
        # the default tower changed: tag the stories explicitly so every
        # story keeps its tower
        for i, s in enumerate(sub.stories):
            if not getattr(s, "tower", ""):
                import copy as _copy
                s2 = _copy.copy(s)
                s2.tower = st[s.name]
                sub.stories[i] = s2


# ---------------------------------------------------------- model tables
def story_tower_get(obj, d) -> str:
    """Story Definitions ``tower`` cell: the story's tower (untagged ->
    the default tower)."""
    towers = (d or {}).get("towers") or []
    t = obj.get("tower") or ""
    if t or not towers:
        return t
    return towers[0].get("name", "")


def story_tower_put(obj, value, d) -> None:
    """Write the ``tower`` cell: the default tower stays untagged (the
    canonical round-trip form)."""
    towers = (d or {}).get("towers") or []
    default = towers[0].get("name", "") if towers else ""
    if not value or value == default:
        obj.pop("tower", None)
    else:
        obj["tower"] = value


def tower_options(d) -> List[str]:
    return [t.get("name", "") for t in (d.get("towers") or [])]


def sync_tower_story_lists(d: dict) -> None:
    """After a Story Definitions edit: rebuild every tower's ``stories``
    list from the story tags (renamed / added / moved stories)."""
    towers = d.get("towers") or []
    if not towers:
        return
    default = towers[0].get("name")
    for t in towers:
        t["stories"] = [s["name"] for s in d.get("stories") or []
                        if (s.get("tower") or default) == t.get("name")]
