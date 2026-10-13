"""Wave F model-level Assign on a selection with a story scope
(CONTRACT "Wave F — select and assign in every view").

``assign(model_dict, selection, assign, scope="one")`` writes ONE
assignment to the selected objects and — for ``scope`` "similar" / "all" —
to the corresponding objects of the other stories of the scope (same kind,
same plan position, shifted by the difference of the story TOP elevations:
:func:`skyframe.core.pickgeom.corresponding_refs`; Story Data "Master Story
/ Similar To", core/similar_stories.py).  It is the scripting / REST twin
of the UI's Assign dialogs with their This Story / Similar Stories / All
Stories selector (``POST /api/assign``).

selection  ``{"members": [uid], "shells": [uid], "links": [uid],
             "points": [[x, y, z]]}`` (the /api/edit selection shape)
assign     exactly one of
    ``{"frame_section": name}``          FrameMember.section (must exist)
    ``{"frame_releases": "Mi,Mj"}``      FrameMember.releases (Mi / Mj tokens)
    ``{"shell_section": name}``          ShellRegion.section (must exist)
    ``{"restraints": [ux, uy, uz, rx, ry, rz]}``  0/1 per DOF on the selected
        points (explicit PointSupports; all zeros removes the support).  When
        the model had no explicit supports, today's automatic base supports
        are first written out explicitly (explicit supports replace the
        automatic base fixity), so the base stays supported — the same rule
        as the Properties panel.
    ``{"link_property": {"link_type": t, "params": {...}, "stiffness": [6]}}``
        LinkMember device (keys present are written)

Pure: works on a deep copy and returns ``(model_dict, summary)``; the
summary lists the objects written per type and the stories touched.
Nothing else in the model changes, so assigning the value an object
already has leaves it byte-identical.
"""

from __future__ import annotations

import copy
from typing import Dict, List, Tuple

from skyframe.core import pickgeom as PG

ASSIGN_KINDS = ("frame_section", "frame_releases", "shell_section",
                "restraints", "link_property")
SCOPES = ("one", "similar", "all")


def _refs_from_selection(m: dict, sel: dict) -> List[dict]:
    if not isinstance(sel, dict):
        raise ValueError("selection must be an object {members, shells, links, points}")
    refs: List[dict] = []
    known = {"members": {o["uid"] for o in m.get("members") or []},
             "shells": {o["uid"] for o in m.get("shells") or []},
             "links": {o["uid"] for o in m.get("links") or []}}
    for key, typ in (("members", "member"), ("shells", "shell"), ("links", "link")):
        for uid in sel.get(key) or []:
            if uid not in known[key]:
                raise ValueError(f"selection.{key}: unknown uid {uid!r}")
            refs.append({"type": typ, "uid": uid})
    joints = PG.model_joints(m)
    for p in sel.get("points") or []:
        if not (isinstance(p, (list, tuple)) and len(p) == 3):
            raise ValueError(f"selection.points: expected [x, y, z], got {p!r}")
        k = PG.joint_key(p)
        if k not in joints:
            raise ValueError(f"selection.points: no joint at {list(p)}")
        refs.append({"type": "joint", "uid": k})
    return refs


def _base_joints(m: dict) -> List[List[float]]:
    zs = [min(mm["pi"][2], mm["pj"][2]) for mm in m.get("members") or []
          if mm.get("kind") == "column"]
    zs += [c[2] for s in m.get("shells") or [] if s.get("kind") == "wall"
           for c in s["corners"]]
    if not zs:
        return []
    z = min(zs)
    out: Dict[str, List[float]] = {}
    for mm in m.get("members") or []:
        for p in (mm["pi"], mm["pj"]):
            if abs(p[2] - z) < 1e-6:
                out.setdefault(PG.joint_key(p), [float(x) for x in p])
    for s in m.get("shells") or []:
        for c in s["corners"]:
            if abs(c[2] - z) < 1e-6:
                out.setdefault(PG.joint_key(c), [float(x) for x in c])
    return list(out.values())


def assign(model: dict, selection: dict, spec: dict,
           scope: str = "one") -> Tuple[dict, dict]:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {list(SCOPES)} (got {scope!r})")
    if not isinstance(spec, dict) or len(spec) != 1 or next(iter(spec)) not in ASSIGN_KINDS:
        raise ValueError(f"assign must hold exactly one of {list(ASSIGN_KINDS)}")
    kind, value = next(iter(spec.items()))
    m = copy.deepcopy(model)
    refs = _refs_from_selection(m, selection)
    if not refs:
        raise ValueError("the selection is empty")
    extra = PG.corresponding_refs(m, refs, scope)["refs"]
    targets = refs + extra
    by_type: Dict[str, List[str]] = {}
    for r in targets:
        by_type.setdefault(r["type"], []).append(r["uid"])

    written = {"members": 0, "shells": 0, "links": 0, "points": 0}
    if kind in ("frame_section", "frame_releases"):
        if kind == "frame_section" and value not in (m.get("sections") or {}):
            raise ValueError(f"unknown frame section {value!r}")
        if kind == "frame_releases":
            toks = [t.strip() for t in str(value).split(",") if t.strip()]
            bad = [t for t in toks if t not in ("Mi", "Mj")]
            if bad:
                raise ValueError(f"frame_releases: unknown token(s) {bad} (Mi, Mj)")
            value = ",".join(toks)
        uids = set(by_type.get("member", []))
        if not uids:
            raise ValueError("no frame objects in the selection")
        for mm in m.get("members") or []:
            if mm["uid"] in uids:
                mm["section" if kind == "frame_section" else "releases"] = value
                written["members"] += 1
    elif kind == "shell_section":
        if value not in (m.get("shell_sections") or {}):
            raise ValueError(f"unknown shell section {value!r}")
        uids = set(by_type.get("shell", []))
        if not uids:
            raise ValueError("no shell objects in the selection")
        for s in m.get("shells") or []:
            if s["uid"] in uids:
                s["section"] = value
                written["shells"] += 1
    elif kind == "restraints":
        if not (isinstance(value, (list, tuple)) and len(value) == 6 and
                all(v in (0, 1, True, False) for v in value)):
            raise ValueError("restraints must be 6 values 0/1 (ux, uy, uz, rx, ry, rz)")
        r = [int(bool(v)) for v in value]
        pts = [PG.joint_point(u) for u in by_type.get("joint", [])]
        if not pts:
            raise ValueError("no joints in the selection (selection.points)")
        sup = m.get("supports") or []
        if not sup:
            base = [1, 1, 1, 0, 0, 0] if m.get("base_fixity") == "pinned" else [1] * 6
            sup = [{"point": p, "restraints": list(base)} for p in _base_joints(m)]
        for p in pts:
            sup = [s for s in sup if not PG._same(s["point"], p, 1e-6)]
            if any(r):
                sup.append({"point": [PG.r6(x) for x in p], "restraints": list(r)})
            written["points"] += 1
        if sup:
            m["supports"] = sup
        else:
            m.pop("supports", None)
    elif kind == "link_property":
        if not isinstance(value, dict) or not value:
            raise ValueError("link_property must be an object {link_type?, params?, stiffness?}")
        allowed = {"link_type", "params", "stiffness"}
        if set(value) - allowed:
            raise ValueError(f"link_property: unknown keys {sorted(set(value) - allowed)}")
        if "stiffness" in value and not (isinstance(value["stiffness"], list) and len(value["stiffness"]) == 6):
            raise ValueError("link_property.stiffness must hold 6 numbers")
        uids = set(by_type.get("link", []))
        if not uids:
            raise ValueError("no links in the selection")
        for lk in m.get("links") or []:
            if lk["uid"] in uids:
                for k, v in value.items():
                    lk[k] = copy.deepcopy(v)
                written["links"] += 1
    stories = sorted({PG.story_of_ref(m, r) or "" for r in targets} - {""})
    summary = {"assign": kind, "scope": scope, "written": written,
               "selected": len(refs), "counterparts": len(extra),
               "stories": stories}
    return m, summary
