"""Pier labels on FRAMES (ETABS Define > Pier Labels; Assign > Frame > Pier
Label: column-as-pier) -- model side.  CONTRACT "Frame pier labels".

* ``FrameMember.pier`` (str, ``""`` = none) puts a column (any non-
  horizontal frame) into a pier.  Frames and wall shells
  (``ShellRegion.pier``) sharing a label form ONE pier per story.
* ``BuildingModel.pier_labels`` (list of names) holds labels that are
  defined but possibly not yet assigned (the ETABS label list).

Serialisation is additive: ``"pier"`` appears on a member only when set and
``"pier_labels"`` only when non-empty, so a legacy model's JSON is
byte-identical.  The force integration lives in
:mod:`skyframe.engine.frame_piers`; the DB-edit table is
:func:`modeltable`.
"""

from __future__ import annotations

import re
from typing import Dict, List

_LABEL_RE = re.compile(r"^[^\s,;][^,;]{0,63}$")


def tag_to_dict(obj) -> dict:
    """``{"pier": label}`` when a frame carries a pier label, else {}."""
    s = getattr(obj, "pier", "") or ""
    return {"pier": s} if s else {}


def model_to_dict(model) -> dict:
    labels = list(getattr(model, "pier_labels", None) or [])
    return {"pier_labels": labels} if labels else {}


def model_from_dict(model, d: dict) -> None:
    """Read per-member ``pier`` tags and the ``pier_labels`` list (shell
    pier labels are read by the legacy ShellRegion path)."""
    tags = {od.get("uid"): od.get("pier")
            for od in d.get("members") or []
            if isinstance(od, dict) and od.get("pier")}
    if tags:
        for m in model.members:
            if m.uid in tags:
                m.pier = str(tags[m.uid])
    labels = d.get("pier_labels")
    if labels is not None:
        if not isinstance(labels, list):
            raise ValueError("pier_labels must be a list of names")
        model.pier_labels = [str(x) for x in labels]


def enabled(model) -> bool:
    """True when any frame member carries a pier label."""
    return any(getattr(m, "pier", "") for m in model.members)


def used_labels(model) -> List[str]:
    out: List[str] = []
    for o in list(model.members) + list(model.shells):
        s = getattr(o, "pier", "") or ""
        if s and s not in out:
            out.append(s)
    return out


def all_labels(model) -> List[str]:
    out = list(getattr(model, "pier_labels", None) or [])
    for s in used_labels(model):
        if s not in out:
            out.append(s)
    return out


def is_horizontal(m) -> bool:
    dz = abs(m.pj[2] - m.pi[2])
    dh = ((m.pj[0] - m.pi[0]) ** 2 + (m.pj[1] - m.pi[1]) ** 2) ** 0.5
    return dz <= 1e-9 * max(1.0, dh)


def validate(model) -> None:
    labels = list(getattr(model, "pier_labels", None) or [])
    seen = set()
    for s in labels:
        if not isinstance(s, str) or not _LABEL_RE.match(s):
            raise ValueError(f"pier label {s!r}: 1-64 characters, no comma "
                             "/ semicolon, no leading space")
        if s in seen:
            raise ValueError(f"pier label {s!r} is defined twice")
        seen.add(s)
    for m in model.members:
        s = getattr(m, "pier", "") or ""
        if not s:
            continue
        if not isinstance(s, str) or not _LABEL_RE.match(s):
            raise ValueError(f"member {m.uid}: bad pier label {s!r}")
        if is_horizontal(m):
            raise ValueError(f"member {m.uid}: pier labels apply to columns "
                             "/ inclined frames only (a horizontal beam is "
                             "a spandrel)")


def summary(model) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {
        s: {"frames": [], "shells": []} for s in all_labels(model)}
    for m in model.members:
        s = getattr(m, "pier", "") or ""
        if s:
            out.setdefault(s, {"frames": [], "shells": []})["frames"].append(
                m.uid)
    for r in model.shells:
        s = getattr(r, "pier", "") or ""
        if s:
            out.setdefault(s, {"frames": [], "shells": []})["shells"].append(
                r.uid)
    return out


# --------------------------------------------------------------------------- #
# Interactive database editing: "Pier Label Assignments" (frames + walls)
# --------------------------------------------------------------------------- #
def modeltable():
    """A DB-edit table over every non-horizontal frame and wall shell:
    object type, uid (read only) and the editable pier label (blank =
    none).  Rows cannot be added or deleted."""
    from skyframe.core import modeltables as MT

    class PierAssignTable(MT.Table):
        can_add = False
        can_delete = False

        def _objs(self, d):
            out = []
            for kind, key in (("Frame", "members"), ("Shell", "shells")):
                for o in d.get(key) or []:
                    if kind == "Shell" and o.get("kind") != "wall":
                        continue
                    if kind == "Frame":
                        pi, pj = o.get("pi") or [0, 0, 0], \
                            o.get("pj") or [0, 0, 0]
                        if abs(float(pj[2]) - float(pi[2])) <= 1e-9:
                            continue
                    out.append((f"{key}:{o.get('uid')}", kind, o))
            return out

        def rows(self, d):
            return [{"_id": rid, "object_type": kind, "uid": o.get("uid"),
                     "pier": o.get("pier") or ""}
                    for rid, kind, o in self._objs(d)]

        def apply(self, d, rows, errs):
            objs = self._objs(d)
            by_id = {rid: o for rid, _k, o in objs}
            by_uid = {(k, o.get("uid")): o for _r, k, o in objs}
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
                if o is None and not (row.get("pier") or ""):
                    continue
                if o is None:
                    errs.append(MT._err(self.key, i, "uid",
                                        "unknown column / wall "
                                        f"{row.get('uid')!r}"))
                    continue
                v = row.get("pier")
                v = "" if v is None else str(v).strip()
                if v and not _LABEL_RE.match(v):
                    errs.append(MT._err(self.key, i, "pier",
                                        f"bad pier label {v!r}"))
                    continue
                is_shell = any(o is oo for _r, k, oo in objs if k == "Shell")
                cur = o.get("pier") or ""
                if cur != v:
                    if v:
                        o["pier"] = v
                    elif is_shell and "pier" in o:
                        o["pier"] = ""          # legacy shell key stays
                    else:
                        o.pop("pier", None)
                    changed += 1
            if len(errs) > n0:
                return {}
            return {"added": 0, "modified": changed, "deleted": 0}

    return PierAssignTable(
        "pier_assignments", "Pier Label Assignments",
        "Model Definition > Assignments", [
            MT.Col("object_type", "Object Type", "text", editable=False),
            MT.Col("uid", "Unique Name", "text", editable=False),
            MT.Col("pier", "Pier", "text", default=""),
        ])
