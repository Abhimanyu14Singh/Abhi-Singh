"""ETABS-style multiple towers (Define > Towers / "Allow multiple towers").

CONTRACT "Multiple towers (Define > Towers)".

Data model
----------
``BuildingModel.towers`` is an optional list of tower entries::

    {"name": "T2", "grid_system": "G2" | None, "base_elevation": 0.0,
     "stories": [...]}

* ``stories`` on INPUT may be a list of story NAMES (references to entries
  of ``model.stories``), a list of story DEFINITIONS ``{"name", "height"}``
  or a plain story-HEIGHT table ``[h1, h2, ...]`` (names ``<tower>-S<i>``).
  Definitions / heights are materialised into ``model.stories`` with
  ``Story.tower`` set and elevations ``base_elevation + cumulative
  height``.  On OUTPUT ``stories`` is always the list of story names of
  the tower (model order), so ``from_dict(to_dict(m))`` is exact.
* ``Story.tower``, ``FrameMember.tower``, ``ShellRegion.tower`` and
  ``LinkMember.tower`` are optional tags ("" = untagged).  Joints are
  tagged with ``BuildingModel.joint_towers = [{"point": [x, y, z],
  "tower": name}]``.
* With towers defined, story names stay unique model-wide (a story belongs
  to exactly one tower); an untagged story belongs to the FIRST tower (the
  default tower).  With ``towers == []`` (default) there is one implicit
  tower and every result is byte-identical to the pre-tower code.

Tower membership rules (deterministic)
--------------------------------------
* story: ``Story.tower`` or the default tower;
* object (member / shell / link): its ``tower`` tag; else the tower of its
  ``story``; else by geometry: the towers having a story at the object's
  top elevation, the one whose plan footprint contains the object's plan
  centroid (first such), else the first candidate, else the default tower;
* joint / point: ``joint_towers`` tag; else a majority vote of the towers
  of the objects meeting at the point, restricted to towers that have a
  story at the point's elevation (ties -> tower order); else footprint;
  else default.  (The engine applies the same rule to its FE nodes.)

The plan footprint of a tower is its grid system's bounding box when it
references one, else the bounding box of the members tied to it by tag or
story.

Per-tower views
---------------
:func:`tower_view` returns a shallow copy of the model restricted to one
tower (its stories, objects, nodal loads / masses, story forces, and its
own grid system as the only grid) with ``towers == []``.  Every
story-based model computation (story masses, autolateral loads, notional
loads, the engine's centre of mass, tributary masses, seismic diagnostics)
is evaluated tower by tower on these views, so each tower behaves exactly
like a single-tower model.
"""
from __future__ import annotations

import copy
import math
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_ZTOL = 1e-6
_NAME_MAX = 60


# ---------------------------------------------------------------- basics
def has_towers(model) -> bool:
    return bool(getattr(model, "towers", None))


def tower_names(model) -> List[str]:
    return [t["name"] for t in (getattr(model, "towers", None) or [])]


def default_tower(model) -> str:
    names = tower_names(model)
    return names[0] if names else ""


def tower_entry(model, name: str) -> dict:
    for t in model.towers:
        if t["name"] == name:
            return t
    raise KeyError(name)


def story_tower(model, story) -> str:
    return getattr(story, "tower", "") or default_tower(model)


def story_tower_map(model) -> Dict[str, str]:
    return {s.name: story_tower(model, s) for s in model.stories}


def tower_stories(model, name: str) -> list:
    return [s for s in model.stories if story_tower(model, s) == name]


def _key(p) -> Tuple[float, float, float]:
    return (round(float(p[0]), 6), round(float(p[1]), 6),
            round(float(p[2]), 6))


def _obj_points(obj) -> List[tuple]:
    if hasattr(obj, "corners"):
        return [tuple(c) for c in obj.corners]
    return [tuple(obj.pi), tuple(obj.pj)]


# ------------------------------------------------------------- footprints
def _grid_by_name(model, name):
    for g in model.effective_grids():
        if g.name == name:
            return g
    return None


def _grid_bbox(g) -> Optional[Tuple[float, float, float, float]]:
    xs: List[float] = []
    ys: List[float] = []
    for ln in g.lines_global():
        for px, py in ln["points"]:
            xs.append(px)
            ys.append(py)
    if not xs:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


class _Ctx:
    """Per-call memo of membership data (the model may change between
    calls, so nothing is cached on the model itself)."""

    def __init__(self, model):
        self.model = model
        self.names = tower_names(model)
        self.default = self.names[0] if self.names else ""
        self.st_tower = story_tower_map(model)
        self.elev_towers: Dict[float, List[str]] = {}
        for s in model.stories:
            lst = self.elev_towers.setdefault(round(s.elevation, 6), [])
            t = self.st_tower[s.name]
            if t not in lst:
                lst.append(t)
        self.joint = {}
        for jt in getattr(model, "joint_towers", None) or []:
            self.joint[_key(jt["point"])] = jt["tower"]
        self._fp: Dict[str, Optional[tuple]] = {}
        self._obj: Dict[int, str] = {}
        self._votes: Optional[Dict[tuple, Counter]] = None

    # towers having a story at elevation z (tower order)
    def cands(self, z: float) -> List[str]:
        out = self.elev_towers.get(round(z, 6))
        if out is None:
            out = []
            for e, lst in self.elev_towers.items():
                if abs(e - z) < _ZTOL:
                    out.extend(t for t in lst if t not in out)
        return [t for t in self.names if t in out]

    def footprint(self, name: str) -> Optional[tuple]:
        if name in self._fp:
            return self._fp[name]
        ent = tower_entry(self.model, name)
        bb = None
        gname = ent.get("grid_system")
        if gname:
            g = _grid_by_name(self.model, gname)
            if g is not None:
                bb = _grid_bbox(g)
        if bb is None:
            xs: List[float] = []
            ys: List[float] = []
            for m in self.model.members:
                if self._tagged_tower(m) == name:
                    for p in _obj_points(m):
                        xs.append(p[0])
                        ys.append(p[1])
            if xs:
                bb = (min(xs), max(xs), min(ys), max(ys))
        self._fp[name] = bb
        return bb

    def in_footprint(self, name: str, x: float, y: float) -> bool:
        bb = self.footprint(name)
        if bb is None:
            return False
        tol = 1e-6
        return (bb[0] - tol <= x <= bb[1] + tol
                and bb[2] - tol <= y <= bb[3] + tol)

    def _tagged_tower(self, obj) -> str:
        t = getattr(obj, "tower", "") or ""
        if t:
            return t
        st = getattr(obj, "story", "") or ""
        return self.st_tower.get(st, "")

    def by_geometry(self, x: float, y: float, z: float) -> str:
        cands = self.cands(z)
        pool = cands or self.names
        for t in pool:
            if self.in_footprint(t, x, y):
                return t
        return cands[0] if cands else self.default

    def obj(self, obj) -> str:
        oid = id(obj)
        if oid in self._obj:
            return self._obj[oid]
        t = self._tagged_tower(obj)
        if not t:
            pts = _obj_points(obj)
            z = max(p[2] for p in pts)
            top = [p for p in pts if abs(p[2] - z) < _ZTOL] or pts
            cx = sum(p[0] for p in top) / len(top)
            cy = sum(p[1] for p in top) / len(top)
            t = self.by_geometry(cx, cy, z)
        self._obj[oid] = t
        return t

    def votes(self) -> Dict[tuple, Counter]:
        if self._votes is None:
            v: Dict[tuple, Counter] = {}
            for o in list(self.model.members) + list(self.model.shells) \
                    + list(self.model.links):
                t = self.obj(o)
                for p in _obj_points(o):
                    v.setdefault(_key(p), Counter())[t] += 1
            self._votes = v
        return self._votes

    def point(self, p) -> str:
        k = _key(p)
        if k in self.joint:
            return self.joint[k]
        return resolve_votes(self, self.votes().get(k), p)


def resolve_votes(ctx: _Ctx, votes: Optional[Counter], p) -> str:
    """Majority vote among towers with a story at p's elevation (ties ->
    tower order), else footprint, else default (shared by model points and
    engine FE nodes)."""
    cands = ctx.cands(p[2])
    if votes:
        pool = [t for t in ctx.names if votes.get(t)
                and (not cands or t in cands)]
        if pool:
            best = max(votes[t] for t in pool)
            for t in pool:
                if votes[t] == best:
                    return t
    return ctx.by_geometry(p[0], p[1], p[2])


def context(model) -> _Ctx:
    return _Ctx(model)


def object_tower(model, obj, ctx: Optional[_Ctx] = None) -> str:
    return (ctx or _Ctx(model)).obj(obj)


def point_tower(model, p, ctx: Optional[_Ctx] = None) -> str:
    return (ctx or _Ctx(model)).point(p)


# ------------------------------------------------------------------ views
def tower_view(model, name: str, ctx: Optional[_Ctx] = None):
    """Shallow copy of ``model`` restricted to tower ``name`` (see module
    doc).  ``towers`` / ``joint_towers`` are emptied so every hook takes
    its legacy single-tower path on the view."""
    ctx = ctx or _Ctx(model)
    v = copy.copy(model)
    v.towers = []
    v.joint_towers = []
    v.stories = [s for s in model.stories if ctx.st_tower[s.name] == name]
    names = {s.name for s in v.stories}
    v.members = [m for m in model.members if ctx.obj(m) == name]
    v.shells = [r for r in model.shells if ctx.obj(r) == name]
    v.links = [lk for lk in model.links if ctx.obj(lk) == name]
    v.nodal_masses = [nm for nm in model.nodal_masses
                      if ctx.point(nm.point) == name]
    pats = {}
    for pn, pat in model.patterns.items():
        p2 = copy.copy(pat)
        p2.nodal_loads = [nl for nl in pat.nodal_loads
                          if ctx.point(nl.point) == name]
        p2.story_forces = [sf for sf in pat.story_forces
                           if sf.story in names]
        pats[pn] = p2
    v.patterns = pats
    ent = tower_entry(model, name)
    g = (_grid_by_name(model, ent["grid_system"])
         if ent.get("grid_system") else None)
    if g is not None:
        v.grid = g
        v.grid_systems = [g]
    elif len(tower_names(model)) > 1:
        # no grid reference: the tower's own member footprint drives the
        # plan centre / extents (legacy member-bbox fallback)
        v.grid = None
        v.grid_systems = []
    return v


def views(model) -> List[Tuple[str, object]]:
    ctx = _Ctx(model)
    return [(n, tower_view(model, n, ctx)) for n in ctx.names]


def merge_story_dicts(model, parts: Iterable[dict], default=0.0) -> dict:
    """Merge per-tower {story: value} dicts into model story order."""
    acc: dict = {}
    for p in parts:
        acc.update(p)
    return {s.name: acc.get(s.name, default) for s in model.stories}


def story_masses(model, self_mass=None, patterns=None) -> Dict[str, float]:
    """``compute_story_masses`` evaluated tower by tower (hook target)."""
    from .model import BuildingModel
    return merge_story_dicts(model, (
        BuildingModel.compute_story_masses(v, self_mass, patterns)
        for _, v in views(model)))


def per_tower_dict(model, fn: Callable, *args, **kw) -> dict:
    """{story: value} function evaluated per tower view, merged."""
    return merge_story_dicts(model, (fn(v, *args, **kw)
                                     for _, v in views(model)))


def per_tower_pattern(model, name: str, call: Callable):
    """Run a story-force pattern generator ``call(view) -> LoadPattern``
    on every tower view and store the merged pattern under ``name``."""
    from .model import LoadPattern
    merged = None
    for _, v in views(model):
        v.patterns = dict(v.patterns)
        sub = call(v)
        if merged is None:
            merged = copy.copy(sub)
            merged.story_forces = []
        merged.story_forces.extend(sub.story_forces)
    if merged is None:                                  # pragma: no cover
        merged = LoadPattern(name, "quake")
    merged.name = name
    model.patterns[name] = merged
    return merged


def _filter_param(val, names: set, all_names: set):
    """Restrict a story-keyed parameter (dict or [{story: ...}]) to one
    tower's stories; any other value passes through unchanged."""
    if isinstance(val, dict) and val and set(val) <= all_names:
        return {k: v for k, v in val.items() if k in names}
    if (isinstance(val, list) and val
            and all(isinstance(r, dict) and r.get("story") in all_names
                    for r in val)):
        return [r for r in val if r.get("story") in names]
    return val


def autolateral_compute(model, fn: Callable, code: str, params: dict
                        ) -> dict:
    """Auto-lateral generator applied per tower (hook target of
    :func:`skyframe.core.autolateral.compute`): every tower distributes its
    OWN base shear (from its own weights, height and plan width) over its
    OWN stories.  The merged summary keeps the single-tower keys (``V`` /
    ``W`` = sums, ``stories`` = all rows, each with ``"tower"``) and adds
    ``"towers": {name: per-tower summary}``."""
    all_names = {s.name for s in model.stories}
    per: Dict[str, dict] = {}
    rows: List[dict] = []
    for tname, v in views(model):
        names = {s.name for s in v.stories}
        kw = {k: _filter_param(val, names, all_names)
              for k, val in params.items()}
        sm = fn(v, **kw)
        per[tname] = sm
        for r in sm["stories"]:
            rows.append(dict(r, tower=tname))
    first = next(iter(per.values()))
    out = {k: val for k, val in first.items()
           if k not in ("stories", "W", "V", "T")}
    Ts = {sm.get("T") for sm in per.values()}
    out["T"] = Ts.pop() if len(Ts) == 1 else None
    Ws = [sm.get("W") for sm in per.values()]
    out["W"] = (sum(Ws) if all(w is not None for w in Ws) else None)
    out["V"] = sum(sm["V"] for sm in per.values())
    out["stories"] = rows
    out["towers"] = per
    return out


# --------------------------------------------------------- serialisation
def tag_to_dict(obj) -> dict:
    t = getattr(obj, "tower", "") or ""
    return {"tower": t} if t else {}


def model_to_dict(model) -> dict:
    """``towers`` / ``joint_towers`` (only when defined)."""
    if not has_towers(model) and not getattr(model, "joint_towers", None):
        return {}
    out: dict = {}
    if has_towers(model):
        st = story_tower_map(model)
        out["towers"] = [
            {"name": t["name"], "grid_system": t.get("grid_system"),
             "base_elevation": float(t.get("base_elevation", 0.0)),
             "stories": [s.name for s in model.stories
                         if st[s.name] == t["name"]]}
            for t in model.towers]
    if getattr(model, "joint_towers", None):
        out["joint_towers"] = [{"point": [float(v) for v in j["point"]],
                                "tower": j["tower"]}
                               for j in model.joint_towers]
    return out


def _num(label: str, v) -> float:
    if (isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v)):
        raise ValueError(f"{label} must be a finite number")
    return float(v)


def normalize_towers(model, raw) -> None:
    """Set ``model.towers`` from a raw list (materialising story
    definitions / height tables into ``model.stories``)."""
    from .model import Story
    if raw is None:
        return
    if not isinstance(raw, list):
        raise ValueError("towers must be a list")
    towers: List[dict] = []
    seen = set()
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ValueError(f"towers[{i}] must be an object")
        name = t.get("name")
        if not isinstance(name, str) or not name.strip() \
                or len(name) > _NAME_MAX:
            raise ValueError(f"towers[{i}].name must be a non-empty string "
                             f"(max {_NAME_MAX} chars)")
        name = name.strip()
        if name in seen:
            raise ValueError(f"duplicate tower name {name!r}")
        seen.add(name)
        gs = t.get("grid_system")
        if gs is not None and not isinstance(gs, str):
            raise ValueError(f"tower {name!r}: grid_system must be a grid "
                             "system name or null")
        base = _num(f"tower {name!r}: base_elevation",
                    t.get("base_elevation", 0.0))
        towers.append({"name": name, "grid_system": gs or None,
                       "base_elevation": base})
    default = towers[0]["name"] if towers else ""
    by_name = {s.name: s for s in model.stories}
    for t, rt in zip(towers, raw):
        sts = rt.get("stories")
        if sts is None:
            continue
        if not isinstance(sts, list):
            raise ValueError(f"tower {t['name']!r}: stories must be a list")
        prev = t["base_elevation"]
        for j, e in enumerate(sts):
            if isinstance(e, str):                       # reference
                s = by_name.get(e)
                if s is None:
                    raise ValueError(f"tower {t['name']!r}: unknown story "
                                     f"{e!r}")
                cur = getattr(s, "tower", "") or ""
                if cur and cur != t["name"]:
                    raise ValueError(f"story {e!r} is tagged tower {cur!r} "
                                     f"but listed under {t['name']!r}")
                if not cur and t["name"] != default:
                    s.tower = t["name"]
                prev = s.elevation
                continue
            if isinstance(e, dict):                      # definition
                sname = e.get("name") or f"{t['name']}-S{j + 1}"
                h = e.get("height")
            else:                                        # height table
                sname = f"{t['name']}-S{j + 1}"
                h = e
            h = _num(f"tower {t['name']!r} story height", h)
            if h <= 0.0:
                raise ValueError(f"tower {t['name']!r}: story heights must "
                                 "be > 0")
            if not isinstance(sname, str) or not sname.strip():
                raise ValueError(f"tower {t['name']!r}: story name must be "
                                 "a non-empty string")
            if sname in by_name:
                raise ValueError(f"story name {sname!r} already exists "
                                 "(story names are unique across towers)")
            prev = prev + h
            s = Story(sname, h, prev)
            s.tower = t["name"]
            model.stories.append(s)
            by_name[sname] = s
    model.towers = towers


def model_from_dict(model, d: dict) -> None:
    """Read towers / joint_towers / per-object ``tower`` tags."""
    stags = {sd.get("name"): sd.get("tower") for sd in d.get("stories") or []
             if isinstance(sd, dict) and sd.get("tower")}
    for s in model.stories:
        if s.name in stags:
            s.tower = str(stags[s.name])
    for key, objs in (("members", model.members), ("shells", model.shells),
                      ("links", model.links)):
        tags = {od.get("uid"): od.get("tower")
                for od in d.get(key) or []
                if isinstance(od, dict) and od.get("tower")}
        if tags:
            for o in objs:
                if o.uid in tags:
                    o.tower = str(tags[o.uid])
    normalize_towers(model, d.get("towers"))
    jt = d.get("joint_towers")
    if jt is not None:
        if not isinstance(jt, list):
            raise ValueError("joint_towers must be a list")
        out = []
        for i, e in enumerate(jt):
            if (not isinstance(e, dict) or not isinstance(e.get("point"),
                                                          (list, tuple))
                    or len(e["point"]) != 3):
                raise ValueError(f"joint_towers[{i}] must be "
                                 "{point: [x, y, z], tower}")
            out.append({"point": [_num("joint_towers point", v)
                                  for v in e["point"]],
                        "tower": str(e.get("tower", ""))})
        model.joint_towers = out


# ------------------------------------------------------------- validation
def validate(model) -> None:
    towers = getattr(model, "towers", None) or []
    names = [t.get("name") for t in towers]
    tagged = ([s for s in model.stories if getattr(s, "tower", "")]
              + [o for o in list(model.members) + list(model.shells)
                 + list(model.links) if getattr(o, "tower", "")])
    jts = getattr(model, "joint_towers", None) or []
    if not towers:
        if tagged or jts:
            raise ValueError("tower tags need model.towers (Define > "
                             "Towers); none are defined")
        return
    if len(set(names)) != len(names):
        raise ValueError("duplicate tower names")
    grids = {g.name for g in model.effective_grids()}
    for t in towers:
        if t.get("grid_system") and t["grid_system"] not in grids:
            raise ValueError(f"tower {t['name']!r}: unknown grid system "
                             f"{t['grid_system']!r}")
        _num(f"tower {t['name']!r}: base_elevation",
             t.get("base_elevation", 0.0))
    nset = set(names)
    for o in tagged:
        if o.tower not in nset:
            raise ValueError(f"{type(o).__name__} "
                             f"{getattr(o, 'uid', getattr(o, 'name', ''))!r}"
                             f": unknown tower {o.tower!r}")
    for j in jts:
        if j.get("tower") not in nset:
            raise ValueError(f"joint_towers: unknown tower "
                             f"{j.get('tower')!r}")
    seen = set()
    for s in model.stories:
        if s.name in seen:
            raise ValueError(f"duplicate story name {s.name!r} (story names "
                             "are unique across towers)")
        seen.add(s.name)
    st = story_tower_map(model)
    for t in towers:
        sts = [s for s in model.stories if st[s.name] == t["name"]]
        if not sts:
            raise ValueError(f"tower {t['name']!r} has no stories")
        for a, b in zip(sts, sts[1:]):
            if not b.elevation > a.elevation + _ZTOL:
                raise ValueError(f"tower {t['name']!r}: story elevations "
                                 "must increase bottom -> top "
                                 f"({a.name!r} -> {b.name!r})")
    for o in list(model.members) + list(model.shells):
        tg = getattr(o, "tower", "") or ""
        sn = getattr(o, "story", "") or ""
        if tg and sn in st and st[sn] != tg:
            raise ValueError(f"{o.uid}: tower {tg!r} contradicts its story "
                             f"{sn!r} (tower {st[sn]!r})")


# ------------------------------------------------------------ convenience
def define_towers(model, towers: list) -> None:
    """Python API: set ``model.towers`` from raw entries (same input forms
    as the JSON ``towers`` key) and validate."""
    normalize_towers(model, towers)
    validate(model)


def summary(model) -> dict:
    """{tower: {"stories", "base_elevation", "grid_system"}} (results /
    UI helper); {} without towers."""
    if not has_towers(model):
        return {}
    st = story_tower_map(model)
    return {t["name"]: {"stories": [s.name for s in model.stories
                                    if st[s.name] == t["name"]],
                        "base_elevation": float(t.get("base_elevation",
                                                      0.0)),
                        "grid_system": t.get("grid_system")}
            for t in model.towers}


# ------------------------------------------------------------------ tables
def decorate_table(model, cols: List[dict], rows: List[dict]):
    """Results tables: with towers, insert a ``tower`` column right after
    the ``story`` column of every story-keyed table (no-op otherwise)."""
    if not has_towers(model) or not any(c.get("key") == "story"
                                        for c in cols):
        return cols, rows
    st = story_tower_map(model)
    out_cols: List[dict] = []
    for c in cols:
        out_cols.append(dict(c))
        if c.get("key") == "story":
            out_cols.append({"key": "tower", "label": "Tower",
                             "quantity": c.get("quantity", "text")})
    out_rows = []
    for r in rows:
        nr = {}
        for k, v in r.items():
            nr[k] = v
            if k == "story":
                nr["tower"] = st.get(v, "")
        out_rows.append(nr)
    return out_cols, out_rows


def chain_break(model, prev_story, story) -> bool:
    """True when a bottom -> top story chain crosses into another tower
    (drift / stiffness chains restart at the tower base)."""
    if not has_towers(model) or prev_story is None:
        return False
    return story_tower(model, prev_story) != story_tower(model, story)


def master_center(model, story) -> Tuple[float, float]:
    """Plan centre of the diaphragm master of ``story`` (tower centre with
    towers, else the model plan centre)."""
    if not has_towers(model):
        return model.plan_center()
    return tower_view(model, story_tower(model, story)).plan_center()


def story_post_dict(d: dict) -> bool:
    """Model-table Story Definitions post-edit with towers: recompute the
    elevations tower by tower (base_elevation + cumulative heights).
    Returns False (caller keeps its legacy logic) without towers."""
    towers = d.get("towers") or []
    if not towers:
        return False
    default = towers[0].get("name")
    for t in towers:
        z = float(t.get("base_elevation", 0.0) or 0.0)
        for s in d.get("stories") or []:
            if (s.get("tower") or default) == t.get("name"):
                z += float(s["height"])
                s["elevation"] = z
    return True
