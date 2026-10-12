"""ETABS master / similar stories (Story Data "Master Story" + "Similar To").

CONTRACT "Master / similar stories, selection and snapping".

``Story.master`` (bool, default False) and ``Story.similar_to`` (str,
default "") are pure MODELLING metadata: the analysis never reads them, so
every result is unchanged.  JSON keys are emitted only when set, so a model
without the feature serializes byte-identically.

Rules (ETABS):

* a master story has ``similar_to == ""``;
* a non-master story is either independent (``similar_to == ""``) or
  similar to exactly one MASTER story (``similar_to`` = its name);
* the *similar group* of a story is its master plus every story similar to
  that master (bottom -> top story order); an independent story's group is
  the story itself.

The drawing / assign / edit scope selector (One / Similar / All stories) of
the UI uses :func:`scope_stories`; ``replicate_members`` is the Python
equivalent of drawing a frame on one story with the "Similar Stories"
scope (used by the tests and available to scripts).
"""

from __future__ import annotations

from typing import Dict, List

SCOPES = ("one", "similar", "all")


# ------------------------------------------------------------- (de)serialize
def trim_story_dict(d: dict) -> dict:
    """Drop the default master / similar_to keys from ``asdict(story)``."""
    if not d.get("master"):
        d.pop("master", None)
    if not d.get("similar_to"):
        d.pop("similar_to", None)
    return d


def model_from_dict(model, d: dict) -> None:
    """Read the per-story ``master`` / ``similar_to`` keys (absent = off)."""
    raw = {sd.get("name"): sd for sd in d.get("stories") or []
           if isinstance(sd, dict)}
    for s in model.stories:
        sd = raw.get(s.name) or {}
        if "master" in sd:
            v = sd["master"]
            if not isinstance(v, bool):
                raise ValueError(f"Story {s.name}: master must be true or "
                                 f"false (got {v!r})")
            s.master = v
        if sd.get("similar_to") is not None:
            s.similar_to = str(sd["similar_to"])


# ------------------------------------------------------------- validation
def validate(model) -> None:
    by = {s.name: s for s in model.stories}
    for s in model.stories:
        m = bool(getattr(s, "master", False))
        sim = getattr(s, "similar_to", "") or ""
        if not isinstance(getattr(s, "master", False), bool):
            raise ValueError(f"Story {s.name}: master must be true or false")
        if not sim:
            continue
        if m:
            raise ValueError(f"Story {s.name}: a master story cannot be "
                             f"similar to another story ({sim!r})")
        if sim == s.name:
            raise ValueError(f"Story {s.name}: similar_to names itself")
        if sim not in by:
            raise ValueError(f"Story {s.name}: similar_to names unknown "
                             f"story {sim!r}")
        if not getattr(by[sim], "master", False):
            raise ValueError(f"Story {s.name}: similar_to must name a "
                             f"master story ({sim!r} is not a master)")


# ------------------------------------------------------------- groups
def similar_group(model, story: str) -> List[str]:
    """Stories that share ``story``'s master (bottom -> top), including
    ``story`` itself; ``[story]`` for an independent story."""
    by = {s.name: s for s in model.stories}
    s = by.get(story)
    if s is None:
        return [story]
    master = s.name if getattr(s, "master", False) else \
        (getattr(s, "similar_to", "") or "")
    if not master:
        return [story]
    return [t.name for t in model.stories
            if t.name == master or (getattr(t, "similar_to", "") or "")
            == master]


def scope_stories(model, story: str, scope: str = "one") -> List[str]:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES} (got {scope!r})")
    if scope == "all":
        return [s.name for s in model.stories]
    if scope == "similar":
        return similar_group(model, story)
    return [story]


def groups(model) -> Dict[str, List[str]]:
    """``{master: [stories of its group]}`` for every master story."""
    return {s.name: similar_group(model, s.name) for s in model.stories
            if getattr(s, "master", False)}


def replicate_members(model, uids, scope: str = "similar") -> List[str]:
    """Copy frame members drawn on one story to the other stories of the
    scope, shifted by the difference in story TOP elevation (the plan
    drawing rule: the object keeps its position relative to its story).

    A member is not copied onto a story that already holds a member of the
    same kind with the same end points.  Returns the new member uids.
    """
    by = {s.name: s for s in model.stories}
    new: List[str] = []
    src = [m for m in model.members if m.uid in set(uids)]
    for m in src:
        if m.story not in by:
            raise ValueError(f"Member {m.uid}: no story to replicate from")
        z0 = by[m.story].elevation
        for st in scope_stories(model, m.story, scope):
            if st == m.story:
                continue
            dz = by[st].elevation - z0
            pi = (m.pi[0], m.pi[1], m.pi[2] + dz)
            pj = (m.pj[0], m.pj[1], m.pj[2] + dz)
            if any(o.kind == m.kind and _same(o.pi, pi) and _same(o.pj, pj)
                   for o in model.members):
                continue
            used = {o.uid for o in model.members}
            k = len(model.members) + 1
            while f"{m.kind[0].upper()}{k}" in used:
                k += 1
            nm = model.add_member(m.kind, m.section, pi, pj, story=st,
                                  uid=f"{m.kind[0].upper()}{k}",
                                  releases=m.releases, angle=m.angle)
            new.append(nm.uid)
    return new


def _same(a, b, tol=1e-6) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
