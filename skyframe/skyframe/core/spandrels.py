"""Spandrel labels (ETABS Define > Spandrel Labels; Assign > Shell / Frame >
Spandrel Label) — model side.

* ``FrameMember.spandrel`` / ``ShellRegion.spandrel`` (str, ``""`` = none)
  label a frame (coupling beam) or a wall shell as part of a spandrel.
  Objects sharing a label form ONE spandrel per story.
* ``BuildingModel.spandrel_labels`` (list of names) holds labels that are
  defined but possibly not yet assigned (the ETABS label list); labels used
  by objects need not be listed.

Serialisation is additive: a ``"spandrel"`` key appears on a member /
shell only when set and ``"spandrel_labels"`` only when non-empty, so a
legacy model's JSON is byte-identical.  The force integration lives in
:mod:`skyframe.engine.spandrels`; the DB-edit table is
:func:`modeltable`.
"""

from __future__ import annotations

import re
from typing import Dict, List

_LABEL_RE = re.compile(r"^[^\s,;][^,;]{0,63}$")


def tag_to_dict(obj) -> dict:
    """``{"spandrel": label}`` when the object carries a label, else {}."""
    s = getattr(obj, "spandrel", "") or ""
    return {"spandrel": s} if s else {}


def model_to_dict(model) -> dict:
    labels = list(getattr(model, "spandrel_labels", None) or [])
    return {"spandrel_labels": labels} if labels else {}


def model_from_dict(model, d: dict) -> None:
    """Read per-object ``spandrel`` tags and the ``spandrel_labels`` list."""
    for key, objs in (("members", model.members), ("shells", model.shells)):
        tags = {od.get("uid"): od.get("spandrel")
                for od in d.get(key) or []
                if isinstance(od, dict) and od.get("spandrel")}
        if tags:
            for o in objs:
                if o.uid in tags:
                    o.spandrel = str(tags[o.uid])
    labels = d.get("spandrel_labels")
    if labels is not None:
        if not isinstance(labels, list):
            raise ValueError("spandrel_labels must be a list of names")
        model.spandrel_labels = [str(x) for x in labels]


def used_labels(model) -> List[str]:
    out: List[str] = []
    for o in list(model.members) + list(model.shells):
        s = getattr(o, "spandrel", "") or ""
        if s and s not in out:
            out.append(s)
    return out


def all_labels(model) -> List[str]:
    """Defined labels first (in order), then any label only used by
    objects."""
    out = list(getattr(model, "spandrel_labels", None) or [])
    for s in used_labels(model):
        if s not in out:
            out.append(s)
    return out


def enabled(model) -> bool:
    """True when any member or WALL shell carries a spandrel label."""
    return any(getattr(m, "spandrel", "") for m in model.members) or any(
        getattr(r, "spandrel", "") and r.kind == "wall"
        for r in model.shells)


def validate(model) -> None:
    """Label syntax, duplicates, and object kinds (ValueError)."""
    labels = list(getattr(model, "spandrel_labels", None) or [])
    seen = set()
    for s in labels:
        if not isinstance(s, str) or not _LABEL_RE.match(s):
            raise ValueError(f"spandrel label {s!r}: 1-64 characters, no "
                             "comma / semicolon, no leading space")
        if s in seen:
            raise ValueError(f"spandrel label {s!r} is defined twice")
        seen.add(s)
    for m in model.members:
        s = getattr(m, "spandrel", "") or ""
        if s and not _LABEL_RE.match(s):
            raise ValueError(f"member {m.uid}: bad spandrel label {s!r}")
    for r in model.shells:
        s = getattr(r, "spandrel", "") or ""
        if not s:
            continue
        if not _LABEL_RE.match(s):
            raise ValueError(f"shell {r.uid}: bad spandrel label {s!r}")
        if r.kind != "wall":
            raise ValueError(f"shell {r.uid}: spandrel labels apply to "
                             "walls only (ETABS spandrels are wall / "
                             "coupling-beam objects)")


# --------------------------------------------------------------------------- #
# Interactive database editing: "Spandrel Label Assignments"
# --------------------------------------------------------------------------- #
def modeltable():
    """A DB-edit table over every frame and wall shell: object type, uid
    (read only) and the editable spandrel label.  Rows cannot be added or
    deleted; a blank label removes the assignment."""
    from skyframe.core import modeltables as MT

    class SpandrelAssignTable(MT.Table):
        can_add = False
        can_delete = False

        def _objs(self, d):
            out = []
            for kind, key in (("Frame", "members"), ("Shell", "shells")):
                for o in d.get(key) or []:
                    if kind == "Shell" and o.get("kind") != "wall":
                        continue
                    out.append((f"{key}:{o.get('uid')}", kind, o))
            return out

        def rows(self, d):
            return [{"_id": rid, "object_type": kind, "uid": o.get("uid"),
                     "spandrel": o.get("spandrel") or ""}
                    for rid, kind, o in self._objs(d)]

        def apply(self, d, rows, errs):
            objs = self._objs(d)
            by_id = {rid: o for rid, _k, o in objs}
            by_uid = {(k, o.get("uid")): o for _r, k, o in objs}
            # CSV / xlsx imports drop the read-only columns (and the ids for
            # a foreign model): fall back to the row order
            positional = len(rows) == len(objs)
            n0, changed = len(errs), 0
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    errs.append(MT._err(self.key, i, None,
                                        "row must be an object"))
                    continue
                o = by_id.get(row.get("_id"))
                if o is None:
                    o = by_uid.get((row.get("object_type"), row.get("uid")))
                if o is None and positional:
                    o = objs[i][2]
                if o is None and not (row.get("spandrel") or ""):
                    continue                     # nothing to assign
                if o is None:
                    errs.append(MT._err(self.key, i, "uid",
                                        "unknown frame / wall "
                                        f"{row.get('uid')!r}"))
                    continue
                v = row.get("spandrel")
                v = "" if v is None else str(v).strip()
                if v and not _LABEL_RE.match(v):
                    errs.append(MT._err(self.key, i, "spandrel",
                                        f"bad spandrel label {v!r}"))
                    continue
                if (o.get("spandrel") or "") != v:
                    if v:
                        o["spandrel"] = v
                    else:
                        o.pop("spandrel", None)
                    changed += 1
            if len(errs) > n0:
                return {}
            return {"added": 0, "modified": changed, "deleted": 0}

    return SpandrelAssignTable(
        "spandrel_assignments", "Spandrel Label Assignments",
        "Model Definition > Assignments", [
            MT.Col("object_type", "Object Type", "text", editable=False),
            MT.Col("uid", "Unique Name", "text", editable=False),
            MT.Col("spandrel", "Spandrel", "text", default=""),
        ])


def summary(model) -> Dict[str, Dict[str, List[str]]]:
    """label -> {"frames": [uids], "shells": [uids]} (UI / report)."""
    out: Dict[str, Dict[str, List[str]]] = {
        s: {"frames": [], "shells": []} for s in all_labels(model)}
    for m in model.members:
        s = getattr(m, "spandrel", "") or ""
        if s:
            out.setdefault(s, {"frames": [], "shells": []})["frames"].append(
                m.uid)
    for r in model.shells:
        s = getattr(r, "spandrel", "") or ""
        if s:
            out.setdefault(s, {"frames": [], "shells": []})["shells"].append(
                r.uid)
    return out
