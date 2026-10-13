"""Structural diff of two SkyFrame models (Tools > Model History).

Works on ``BuildingModel.to_dict()`` dictionaries (or the SPA's working
model JSON, which has the same shape) and never mutates its inputs.

Object types (``TYPES``) and how objects are matched:

* ``frames`` / ``shells`` / ``links`` — by ``uid``; objects left unmatched
  on both sides are then paired by GEOMETRY (end points / corner set,
  orientation-free, rounded to ``GEOM_DIGITS`` decimals) so a renamed or
  re-drawn object reads as "changed" (``match: "geometry"``, with a
  ``uid`` field change) rather than removed + added.
* ``joints`` — derived from the geometry (frame / link ends, shell
  corners, supports, springs, nodal masses), identified by coordinates;
  fields = the explicit nodal mass at that point.
* ``supports`` — point restraints, point springs (by point) and line
  springs (by end points).
* ``sections`` (frame, shell, designer, built-up ... sections) and
  ``materials`` — by name; unmatched names whose content is identical are
  paired as a rename (``match: "content"``).
* ``loads`` — one object per load pattern (its definition fields) plus
  one object per (pattern, load list, target object) holding the list of
  loads that pattern puts on that object (``DEAD | area_loads | F1-A1``).
* ``cases`` (every ``*_cases`` collection; key ``kind/name``), ``combos``
  (``combos`` + ``rs_combos``), ``stories`` (by name, renames paired by
  elevation) and ``grids`` (grid systems by name).
* ``options`` — every other top-level key; dict-of-dict collections
  (groups, functions, spring properties, ...) are split per entry.

Derived keys (``story_masses``, the legacy ``grid`` copy, member
``length``) are ignored.  Numbers compare with a relative tolerance
``tol`` (default 1e-12, i.e. only real edits show).

:func:`diff` returns ``{"summary", "items", "n_items", "truncated",
"elapsed_ms"}``; :func:`apply_restore` reverts selected objects (or all)
of a "current" model dict to their state in a reference model dict.
CONTRACT "Model history and visual diff".
"""

from __future__ import annotations

import copy
import json
import math
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

TYPES = ("joints", "frames", "shells", "links", "supports", "sections",
         "materials", "loads", "cases", "combos", "stories", "grids",
         "options")

TYPE_LABELS = {"joints": "Joints", "frames": "Frames", "shells": "Shells",
               "links": "Links", "supports": "Supports",
               "sections": "Sections", "materials": "Materials",
               "loads": "Loads", "cases": "Load cases",
               "combos": "Combinations", "stories": "Stories",
               "grids": "Grids", "options": "Options"}

GEOM_DIGITS = 6
#: top-level keys never compared (derived from the rest of the model)
DERIVED_KEYS = frozenset({"story_masses", "grid", "_mock_params"})
#: section-like collections (top-level dicts keyed by section name)
SECTION_KEYS = ("sections", "shell_sections", "designer_sections",
                "builtup_sections", "np_sections")
_HANDLED = frozenset({"members", "shells", "links", "supports",
                      "spring_supports", "line_springs", "nodal_masses",
                      "materials", "patterns", "combos", "rs_combos",
                      "stories", "grid_systems"} | set(SECTION_KEYS))
_MAX_VALUE_CHARS = 160


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _pt(p) -> Tuple[float, ...]:
    try:
        return tuple(round(float(c), GEOM_DIGITS) + 0.0 for c in p)
    except (TypeError, ValueError):
        return tuple()


def _ptkey(p) -> str:
    t = _pt(p)
    return ",".join("%.12g" % c for c in t)


def _is_case_key(k: str) -> bool:
    return k == "cases" or k.endswith("_cases")


def _case_kind(k: str) -> str:
    return "static" if k == "cases" else k[:-len("_cases")]


def _short(v: Any) -> Any:
    """A JSON-safe, size-bounded copy of a field value for the change
    table (long lists / dicts become a one-line summary)."""
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    try:
        s = json.dumps(v, sort_keys=True)
    except (TypeError, ValueError):
        s = str(v)
    if len(s) <= _MAX_VALUE_CHARS:
        return v
    if isinstance(v, list):
        return f"[{len(v)} items]"
    if isinstance(v, dict):
        return f"{{{len(v)} keys}}"
    return s[:_MAX_VALUE_CHARS] + "…"


def _num_eq(a: float, b: float, tol: float) -> bool:
    if a == b:
        return True
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def field_changes(a: Any, b: Any, tol: float = 1e-12, path: str = "",
                  out: Optional[List[dict]] = None,
                  limit: int = 200) -> List[dict]:
    """Field-level differences between two JSON values as
    ``[{"path", "a", "b"}]`` (``a``/``b`` is ``None`` for a missing key;
    ``"<absent>"`` paths are reported with ``present_a`` / ``present_b``)."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if a is b:
        return out
    na = isinstance(a, (int, float)) and not isinstance(a, bool)
    nb = isinstance(b, (int, float)) and not isinstance(b, bool)
    if na and nb:
        if not _num_eq(float(a), float(b), tol):
            out.append({"path": path, "a": a, "b": b})
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        if a == b:
            return out
        for k in list(a.keys()) + [k for k in b if k not in a]:
            p = f"{path}.{k}" if path else str(k)
            if k not in b:
                out.append({"path": p, "a": _short(a[k]), "b": None,
                            "present_b": False})
            elif k not in a:
                out.append({"path": p, "a": None, "b": _short(b[k]),
                            "present_a": False})
            else:
                field_changes(a[k], b[k], tol, p, out, limit)
            if len(out) >= limit:
                break
        return out
    if isinstance(a, list) and isinstance(b, list):
        if a == b:
            return out
        if len(a) == len(b) and (len(a) <= 12 or all(
                isinstance(x, (dict, list)) for x in a[:3])):
            for i, (x, y) in enumerate(zip(a, b)):
                field_changes(x, y, tol, f"{path}[{i}]", out, limit)
                if len(out) >= limit:
                    break
            return out
        # different lengths (or long scalar lists): compare elementwise with
        # the tolerance before reporting the whole list
        if len(a) == len(b) and all(
                isinstance(x, (int, float)) and isinstance(y, (int, float))
                and _num_eq(float(x), float(y), tol) for x, y in zip(a, b)):
            return out
        out.append({"path": path, "a": _short(a), "b": _short(b)})
        return out
    if a != b:
        out.append({"path": path, "a": _short(a), "b": _short(b)})
    return out


# --------------------------------------------------------------------------- #
# object extraction
# --------------------------------------------------------------------------- #
class _Obj:
    __slots__ = ("key", "rec", "label", "geo", "geom", "ref", "order",
                 "content")

    def __init__(self, key, rec, label=None, geo=None, geom=None, ref=None,
                 order=0, content=None):
        self.key = key
        self.rec = rec
        self.label = label or key
        self.geo = geo            # geometric-fallback match key (or None)
        self.geom = geom          # drawing geometry for the views
        self.ref = ref            # {"kind", "uid"} for the SPA selection
        self.order = order
        self.content = content    # rename-by-content match key


def _uid_objects(items, kind: str, geo_fn, geom_fn) -> Dict[str, _Obj]:
    out: Dict[str, _Obj] = {}
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        uid = str(it.get("uid", f"#{i}"))
        rec = {k: v for k, v in it.items() if k != "length"}
        out[uid] = _Obj(uid, rec, uid, geo_fn(it), geom_fn(it),
                        {"kind": kind, "uid": uid}, i)
    return out


def _frame_geo(m):
    a, b = _pt(m.get("pi", ())), _pt(m.get("pj", ()))
    return ("F",) + tuple(sorted((a, b))) if a and b else None


def _frame_geom(m):
    return {"pi": m.get("pi"), "pj": m.get("pj")}


def _shell_geo(s):
    pts = [_pt(c) for c in (s.get("corners") or [])]
    return ("S",) + tuple(sorted(pts)) if pts else None


def _shell_geom(s):
    return {"corners": s.get("corners")}


def _joints(d: dict) -> Dict[str, _Obj]:
    pts: Dict[str, list] = {}
    order: Dict[str, int] = {}

    def add(p):
        if p is None:
            return
        k = _ptkey(p)
        if k and k not in pts:
            pts[k] = [float(c) for c in p]
            order[k] = len(order)

    for m in d.get("members") or []:
        add(m.get("pi")); add(m.get("pj"))
    for lk in d.get("links") or []:
        add(lk.get("pi")); add(lk.get("pj"))
    for s in d.get("shells") or []:
        for c in s.get("corners") or []:
            add(c)
    for key in ("supports", "spring_supports", "nodal_masses"):
        for s in d.get(key) or []:
            add(s.get("point"))
    masses: Dict[str, dict] = {}
    for nm in d.get("nodal_masses") or []:
        k = _ptkey(nm.get("point"))
        masses.setdefault(k, {}).update(
            {f: v for f, v in nm.items() if f != "point"})
    out = {}
    for k, p in pts.items():
        rec = {"point": [round(c, GEOM_DIGITS) for c in p]}
        if k in masses:
            rec["mass"] = masses[k]
        out[k] = _Obj(k, rec, f"({k})", None, {"point": p},
                      {"kind": "joint", "point": p}, order[k])
    return out


def _supports(d: dict) -> Dict[str, _Obj]:
    out: Dict[str, _Obj] = {}
    for i, s in enumerate(d.get("supports") or []):
        k = "restraint@" + _ptkey(s.get("point"))
        out[k] = _Obj(k, s, f"Restraint ({_ptkey(s.get('point'))})", None,
                      {"point": s.get("point")},
                      {"kind": "joint", "point": s.get("point")}, i)
    for i, s in enumerate(d.get("spring_supports") or []):
        k = "spring@" + _ptkey(s.get("point"))
        out[k] = _Obj(k, s, f"Point spring ({_ptkey(s.get('point'))})", None,
                      {"point": s.get("point")},
                      {"kind": "joint", "point": s.get("point")}, i)
    for i, s in enumerate(d.get("line_springs") or []):
        k = ("line@" + _ptkey(s.get("p1")) + "|" + _ptkey(s.get("p2")))
        out[k] = _Obj(k, s, f"Line spring ({_ptkey(s.get('p1'))} – "
                      f"{_ptkey(s.get('p2'))})", None,
                      {"pi": s.get("p1"), "pj": s.get("p2")},
                      {"kind": "linespring"}, i)
    return out


def _content_key(v) -> str:
    if isinstance(v, dict):
        v = {k: x for k, x in v.items() if k != "name"}
    try:
        return json.dumps(v, sort_keys=True)
    except (TypeError, ValueError):
        return repr(v)


def _named(d: dict, keys: Iterable[str], kind_fn=None) -> Dict[str, _Obj]:
    out: Dict[str, _Obj] = {}
    for key in keys:
        coll = d.get(key)
        if not isinstance(coll, dict):
            continue
        kind = kind_fn(key) if kind_fn else key
        for i, (name, v) in enumerate(coll.items()):
            k = f"{kind}/{name}" if kind_fn else (
                name if key == keys[0] else f"{key}/{name}")
            out[k] = _Obj(k, v, str(name), None, None,
                          {"kind": kind, "name": name}, i, _content_key(v))
    return out


def _load_target(item: dict) -> Tuple[str, str]:
    if "member_uid" in item:
        return "frame", str(item["member_uid"])
    if "region_uid" in item:
        return "shell", str(item["region_uid"])
    if "uid" in item:
        return "object", str(item["uid"])
    if "point" in item:
        return "joint", _ptkey(item["point"])
    if "story" in item:
        return "story", str(item["story"])
    return "", ""


def _is_load_list(v) -> bool:
    """A pattern entry that holds loads: a list of dicts (empty included)."""
    return isinstance(v, list) and all(isinstance(x, dict) for x in v)


def _loads(d: dict) -> Dict[str, _Obj]:
    out: Dict[str, _Obj] = {}
    pats = d.get("patterns") or {}
    if not isinstance(pats, dict):
        return out
    order = 0
    for pname, pat in pats.items():
        if not isinstance(pat, dict):
            continue
        definition = {}
        groups: Dict[Tuple[str, str, str], list] = {}
        for k, v in pat.items():
            if _is_load_list(v):
                for item in v:
                    tk, tid = _load_target(item)
                    groups.setdefault((k, tk, tid), []).append(item)
            else:
                definition[k] = v
        out[pname] = _Obj(pname, definition, f"Pattern {pname}", None, None,
                          {"kind": "pattern", "name": pname}, order)
        order += 1
        for (lk, tk, tid), items in groups.items():
            key = f"{pname}|{lk}|{tk}:{tid}"
            ref = None
            if tk == "frame":
                ref = {"kind": "member", "uid": tid}
            elif tk == "shell":
                ref = {"kind": "shell", "uid": tid}
            elif tk == "joint":
                ref = {"kind": "joint"}
            out[key] = _Obj(key, {"items": items},
                            f"{pname} · {lk.replace('_', ' ')} · {tid}",
                            None, None, ref, order)
            order += 1
    return out


def _stories(d: dict) -> Dict[str, _Obj]:
    out = {}
    for i, s in enumerate(d.get("stories") or []):
        if not isinstance(s, dict):
            continue
        name = str(s.get("name", f"#{i}"))
        geo = ("Z", round(float(s.get("elevation", 0.0) or 0.0), GEOM_DIGITS))
        out[name] = _Obj(name, s, name, geo, None,
                         {"kind": "story", "name": name}, i)
    return out


def _grids(d: dict) -> Dict[str, _Obj]:
    out = {}
    for i, g in enumerate(d.get("grid_systems") or []):
        if not isinstance(g, dict):
            continue
        name = str(g.get("name", f"#{i}"))
        out[name] = _Obj(name, g, name, None, None,
                         {"kind": "grid", "name": name}, i, _content_key(g))
    return out


def _options(d: dict) -> Dict[str, _Obj]:
    out = {}
    i = 0
    for k, v in d.items():
        if k in DERIVED_KEYS or k in _HANDLED or _is_case_key(k):
            continue
        if (isinstance(v, dict) and v
                and all(isinstance(x, dict) for x in v.values())):
            for name, x in v.items():
                key = f"{k}/{name}"
                out[key] = _Obj(key, x, f"{k.replace('_', ' ')}: {name}",
                                None, None, {"kind": "option", "key": k,
                                             "name": name}, i)
                i += 1
        else:
            out[k] = _Obj(k, v, k.replace("_", " "), None, None,
                          {"kind": "option", "key": k}, i)
            i += 1
    return out


def collect(d: dict, types: Optional[Iterable[str]] = None
            ) -> Dict[str, Dict[str, _Obj]]:
    """``{type: {key: _Obj}}`` of one model dict."""
    want = set(types or TYPES)
    out: Dict[str, Dict[str, _Obj]] = {}
    if "frames" in want:
        out["frames"] = _uid_objects(d.get("members"), "member",
                                     _frame_geo, _frame_geom)
    if "shells" in want:
        out["shells"] = _uid_objects(d.get("shells"), "shell",
                                     _shell_geo, _shell_geom)
    if "links" in want:
        out["links"] = _uid_objects(d.get("links"), "link",
                                    _frame_geo, _frame_geom)
    if "joints" in want:
        out["joints"] = _joints(d)
    if "supports" in want:
        out["supports"] = _supports(d)
    if "sections" in want:
        out["sections"] = _named(d, SECTION_KEYS)
    if "materials" in want:
        out["materials"] = _named(d, ("materials",))
    if "loads" in want:
        out["loads"] = _loads(d)
    if "cases" in want:
        out["cases"] = _named(d, tuple(k for k in d if _is_case_key(k)),
                              _case_kind)
    if "combos" in want:
        out["combos"] = _named(d, ("combos", "rs_combos"))
    if "stories" in want:
        out["stories"] = _stories(d)
    if "grids" in want:
        out["grids"] = _grids(d)
    if "options" in want:
        out["options"] = _options(d)
    return out


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #
def _item(tname, status, oa: Optional[_Obj], ob: Optional[_Obj],
          fields=None, match="key") -> dict:
    o = ob or oa
    it = {"type": tname, "key": o.key, "label": o.label, "status": status,
          "match": match}
    if oa is not None and ob is not None and oa.key != ob.key:
        it["key_a"] = oa.key
        it["key_b"] = ob.key
        it["label"] = f"{oa.label} → {ob.label}"
    if fields is not None:
        it["fields"] = fields
    ref = (ob.ref if ob is not None else oa.ref)
    if ref:
        it["ref"] = ref
    if oa is not None and oa.ref and ob is not None and ob.ref \
            and oa.ref != ob.ref:
        it["ref_a"] = oa.ref
    geom = None
    if status == "removed" and oa is not None:
        geom = oa.geom
    elif ob is not None:
        geom = ob.geom
    if geom:
        it["geom"] = geom
    if status == "changed" and oa is not None and oa.geom and \
            ob is not None and oa.geom != ob.geom:
        it["geom_a"] = oa.geom
    return it


def _diff_type(tname: str, A: Dict[str, _Obj], B: Dict[str, _Obj],
               tol: float, items: List[dict], field_limit: int) -> dict:
    added = removed = changed = unchanged = 0
    rem_keys = [k for k in A if k not in B]
    add_keys = [k for k in B if k not in A]
    # common keys
    for k, oa in A.items():
        ob = B.get(k)
        if ob is None:
            continue
        if oa.rec == ob.rec:
            unchanged += 1
            continue
        f = field_changes(oa.rec, ob.rec, tol, limit=field_limit)
        if not f:
            unchanged += 1
            continue
        changed += 1
        items.append(_item(tname, "changed", oa, ob, f))
    # geometric / content fallback for the unmatched ones
    paired_a, paired_b = set(), set()
    if rem_keys and add_keys:
        for attr, match in (("geo", "geometry"), ("content", "content")):
            idx: Dict[Any, List[str]] = {}
            for k in add_keys:
                if k in paired_b:
                    continue
                g = getattr(B[k], attr)
                if g is not None:
                    idx.setdefault(g, []).append(k)
            for k in rem_keys:
                if k in paired_a:
                    continue
                g = getattr(A[k], attr)
                cand = idx.get(g) if g is not None else None
                if not cand:
                    continue
                kb = cand.pop(0)
                paired_a.add(k)
                paired_b.add(kb)
                oa, ob = A[k], B[kb]
                ra = dict(oa.rec) if isinstance(oa.rec, dict) else oa.rec
                rb = dict(ob.rec) if isinstance(ob.rec, dict) else ob.rec
                f = field_changes(ra, rb, tol, limit=field_limit)
                if not any(x["path"] in ("uid", "name") for x in f):
                    f.insert(0, {"path": "uid" if tname in (
                        "frames", "shells", "links") else "name",
                        "a": oa.key, "b": ob.key})
                changed += 1
                items.append(_item(tname, "changed", oa, ob, f, match))
    for k in rem_keys:
        if k not in paired_a:
            removed += 1
            items.append(_item(tname, "removed", A[k], None))
    for k in add_keys:
        if k not in paired_b:
            added += 1
            items.append(_item(tname, "added", None, B[k]))
    return {"added": added, "removed": removed, "changed": changed,
            "unchanged": unchanged}


def diff(a: dict, b: dict, types: Optional[Iterable[str]] = None,
         tol: float = 1e-12, max_items: Optional[int] = None,
         field_limit: int = 50) -> dict:
    """Structural diff ``a`` (reference / older) -> ``b`` (newer).

    Returns ``{"summary": {type: {added, removed, changed, unchanged}},
    "items": [...], "n_items", "truncated", "equal", "elapsed_ms"}``; each
    item is ``{type, key, label, status: "added"|"removed"|"changed",
    match: "key"|"geometry"|"content", fields?: [{path, a, b}], ref?,
    geom?, key_a?/key_b? (paired by geometry/content)}``.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise ValueError("both models must be JSON objects")
    t0 = time.perf_counter()
    types = [t for t in (types or TYPES) if t in TYPES]
    CA, CB = collect(a, types), collect(b, types)
    summary: Dict[str, dict] = {}
    items: List[dict] = []
    for t in types:
        summary[t] = _diff_type(t, CA.get(t, {}), CB.get(t, {}), tol, items,
                                field_limit)
    n = len(items)
    truncated = False
    if max_items is not None and n > max_items:
        items = items[:max_items]
        truncated = True
    equal = all(s["added"] == s["removed"] == s["changed"] == 0
                for s in summary.values())
    return {"summary": summary, "items": items, "n_items": n,
            "truncated": truncated, "equal": equal,
            "types": list(types),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000.0, 3)}


# --------------------------------------------------------------------------- #
# restore (selected objects of ``ref`` back into ``cur``)
# --------------------------------------------------------------------------- #
def _restore_list(cur: list, ref: list, keyf, sel_a: set, sel_b: set
                  ) -> list:
    """Revert the list ``cur`` to ``ref`` for the selected keys: ``sel_a``
    = keys (in ``ref``) to bring back / restore; ``sel_b`` = keys (in
    ``cur``) to drop or replace.  Unselected items keep their place;
    re-added items go after their predecessor in ``ref``."""
    ref_by = {keyf(x, i): x for i, x in enumerate(ref)}
    ref_keys = [keyf(x, i) for i, x in enumerate(ref)]
    out: List[Tuple[str, Any]] = []
    for i, x in enumerate(cur):
        k = keyf(x, i)
        if k in sel_b:
            continue
        out.append((k, x))
    present = {k for k, _ in out}
    for j, k in enumerate(ref_keys):
        if k not in sel_a or k in present:
            continue
        # insert after the nearest preceding ref item already in out
        pos = 0
        for p in range(j - 1, -1, -1):
            pk = ref_keys[p]
            hit = next((n for n, (ok, _) in enumerate(out) if ok == pk),
                       None)
            if hit is not None:
                pos = hit + 1
                break
        out.insert(pos, (k, copy.deepcopy(ref_by[k])))
        present.add(k)
    return [x for _, x in out]


def apply_restore(cur: dict, ref: dict,
                  items: Optional[List[dict]] = None) -> Tuple[dict, dict]:
    """Return ``(new_model_dict, report)``: ``cur`` with the selected diff
    items (``[{type, key}]`` from :func:`diff` (ref -> cur); None = every
    difference) reverted to their state in ``ref``.

    Restoring every difference reproduces ``ref``'s objects; derived keys
    (``story_masses``, ``grid``) are recomputed by ``from_dict`` later.
    Joints are derived from their objects: a joint item restores only its
    nodal mass."""
    dd = diff(ref, cur)
    wanted = None
    if items is not None:
        wanted = {(str(i.get("type")), str(i.get("key"))) for i in items}
    chosen = [it for it in dd["items"]
              if wanted is None or (it["type"], it["key"]) in wanted
              or (it["type"], it.get("key_b", "")) in wanted
              or (it["type"], it.get("key_a", "")) in wanted]
    new = copy.deepcopy(cur)
    report = {"restored": [], "skipped": []}
    if items is not None:
        found = {(it["type"], k) for it in chosen
                 for k in (it["key"], it.get("key_a"), it.get("key_b")) if k}
        for w in wanted:
            if w not in found:
                report["skipped"].append({"type": w[0], "key": w[1],
                                          "reason": "no difference"})
    by_type: Dict[str, List[dict]] = {}
    for it in chosen:
        by_type.setdefault(it["type"], []).append(it)

    def ab_keys(lst):
        """(keys in ref to restore, keys in cur to drop/replace)."""
        sa, sb = set(), set()
        for it in lst:
            if it["status"] == "added":
                sb.add(it["key"])
            elif it["status"] == "removed":
                sa.add(it["key"])
            else:
                sa.add(it.get("key_a", it["key"]))
                sb.add(it.get("key_b", it["key"]))
        return sa, sb

    uid_key = (lambda x, i: str(x.get("uid", f"#{i}"))
               if isinstance(x, dict) else f"#{i}")
    for tname, coll in (("frames", "members"), ("shells", "shells"),
                        ("links", "links")):
        if tname in by_type:
            sa, sb = ab_keys(by_type[tname])
            new[coll] = _restore_list(new.get(coll) or [],
                                      ref.get(coll) or [], uid_key, sa, sb)
    if "stories" in by_type:
        sa, sb = ab_keys(by_type["stories"])
        nk = (lambda x, i: str(x.get("name", f"#{i}")))
        new["stories"] = _restore_list(new.get("stories") or [],
                                       ref.get("stories") or [], nk, sa, sb)
    if "grids" in by_type:
        sa, sb = ab_keys(by_type["grids"])
        nk = (lambda x, i: str(x.get("name", f"#{i}")))
        new["grid_systems"] = _restore_list(new.get("grid_systems") or [],
                                            ref.get("grid_systems") or [],
                                            nk, sa, sb)
        if new["grid_systems"]:
            new["grid"] = copy.deepcopy(new["grid_systems"][0])
    if "supports" in by_type:
        sa, sb = ab_keys(by_type["supports"])
        for prefix, coll, kf in (
                ("restraint@", "supports",
                 lambda x, i: "restraint@" + _ptkey(x.get("point"))),
                ("spring@", "spring_supports",
                 lambda x, i: "spring@" + _ptkey(x.get("point"))),
                ("line@", "line_springs",
                 lambda x, i: "line@" + _ptkey(x.get("p1")) + "|"
                 + _ptkey(x.get("p2")))):
            a2 = {k for k in sa if k.startswith(prefix)}
            b2 = {k for k in sb if k.startswith(prefix)}
            if a2 or b2:
                new[coll] = _restore_list(new.get(coll) or [],
                                          ref.get(coll) or [], kf, a2, b2)
    if "joints" in by_type:
        # a joint's own data is its nodal mass
        sa, sb = ab_keys(by_type["joints"])
        keys = sa | sb
        kf = (lambda x, i: _ptkey(x.get("point")))
        cur_nm = new.get("nodal_masses") or []
        ref_nm = ref.get("nodal_masses") or []
        if any(kf(x, 0) in keys for x in cur_nm + ref_nm):
            new["nodal_masses"] = _restore_list(cur_nm, ref_nm, kf, keys,
                                                keys)
        for it in by_type["joints"]:
            if it["status"] != "changed":
                report["skipped"].append({
                    "type": "joints", "key": it["key"],
                    "reason": "joints follow the objects that use them"})
    # named collections ({coll: {name: value}})
    touched: Dict[str, None] = {}

    def restore_named(lst, keyfn):
        for it in lst:
            ca, na = keyfn(it.get("key_a", it["key"]))
            cb, nb = keyfn(it.get("key_b", it["key"]))
            if it["status"] in ("added", "changed") and \
                    isinstance(new.get(cb), dict):
                new[cb].pop(nb, None)
                touched[cb] = None
            if it["status"] in ("removed", "changed"):
                src = ref.get(ca) or {}
                if na in src:
                    if not isinstance(new.get(ca), dict):
                        new[ca] = {}
                    new[ca][na] = copy.deepcopy(src[na])
                    touched[ca] = None

    def sec_key(k):
        if "/" in k and k.split("/", 1)[0] in SECTION_KEYS[1:]:
            c, n = k.split("/", 1)
            return c, n
        return "sections", k

    def case_key(k):
        kind, name = k.split("/", 1)
        return ("cases" if kind == "static" else kind + "_cases"), name

    if "sections" in by_type:
        restore_named(by_type["sections"], sec_key)
    if "materials" in by_type:
        restore_named(by_type["materials"], lambda k: ("materials", k))
    if "combos" in by_type:
        restore_named(by_type["combos"],
                      lambda k: (tuple(k.split("/", 1)) if k.startswith(
                          "rs_combos/") else ("combos", k)))
    if "cases" in by_type:
        restore_named(by_type["cases"], case_key)
    if "options" in by_type:
        for it in by_type["options"]:
            key = it["key"]
            if "name" in (it.get("ref") or {}) and "/" in key:
                restore_named([it], lambda k: tuple(k.split("/", 1)))
            elif key in ref:
                new[key] = copy.deepcopy(ref[key])
            else:
                new.pop(key, None)
    for coll in touched:                     # ref order for kept names
        if isinstance(new.get(coll), dict) and isinstance(ref.get(coll),
                                                          dict):
            rank = {k: i for i, k in enumerate(ref[coll])}
            cur = list(new[coll])
            pos = {k: i for i, k in enumerate(cur)}
            n = len(rank)
            new[coll] = {k: new[coll][k] for k in sorted(
                cur, key=lambda k: (rank.get(k, n + pos[k])))}
    if "loads" in by_type:
        _restore_loads(new, ref, by_type["loads"])
    report["restored"] = [{"type": it["type"], "key": it["key"],
                           "status": it["status"]} for it in chosen]
    return new, report


def _restore_loads(new: dict, ref: dict, lst: List[dict]) -> None:
    npat = new.setdefault("patterns", {})
    rpat = ref.get("patterns") or {}
    for it in lst:
        key = it["key"]
        if "|" not in key:                       # pattern definition
            if it["status"] == "added":
                npat.pop(key, None)
                continue
            src = rpat.get(key)
            if src is None:
                continue
            if it["status"] == "removed" or key not in npat:
                npat[key] = copy.deepcopy(src)
                continue
            cur = npat[key]
            for k, v in src.items():
                if not _is_load_list(v):
                    cur[k] = copy.deepcopy(v)
                elif k not in cur:
                    cur[k] = []
            for k in [k for k in cur if k not in src
                      and not _is_load_list(cur[k])]:
                cur.pop(k, None)
            continue
        pname, lk, tgt = key.split("|", 2)
        tk, tid = tgt.split(":", 1)
        if pname not in npat:
            if pname in rpat:
                npat[pname] = copy.deepcopy(rpat[pname])
            continue
        cur = npat[pname]

        def match(x):
            return _load_target(x) == (tk, tid)

        kept = [x for x in (cur.get(lk) or []) if not match(x)]
        back = [copy.deepcopy(x) for x in ((rpat.get(pname) or {}).get(lk)
                                           or []) if match(x)]
        # put the restored items back where the first one was
        old = cur.get(lk) or []
        first = next((i for i, x in enumerate(old) if match(x)), None)
        if first is None:
            # position from ref: after the items preceding it there
            rl = (rpat.get(pname) or {}).get(lk) or []
            ri = next((i for i, x in enumerate(rl) if match(x)), len(rl))
            before = sum(1 for x in rl[:ri] if not match(x))
            first = min(before, len(kept))
        else:
            first = sum(1 for x in old[:first] if not match(x))
        cur[lk] = kept[:first] + back + kept[first:]


def model_counts(d: dict) -> dict:
    """Cheap object counts for the history list."""
    return {"stories": len(d.get("stories") or []),
            "frames": len(d.get("members") or []),
            "shells": len(d.get("shells") or []),
            "links": len(d.get("links") or []),
            "patterns": len(d.get("patterns") or {}),
            "cases": sum(len(v) for k, v in d.items()
                         if _is_case_key(k) and isinstance(v, dict)),
            "combos": len(d.get("combos") or {})}
