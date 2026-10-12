"""W8 Interactive Database Editing coverage: the model tables that the
v1.x catalogue (:mod:`skyframe.core.modeltables`) did not have.

CONTRACT "Master / similar stories, selection and snapping" (section
"Database-editing coverage").  One hook line at the end of
``modeltables._build_catalogue`` calls :func:`extend_catalogue`, which

* appends columns to existing tables:
  ``stories`` (master, similar_to), ``shell_sections`` (shell_type),
  ``frame_objects`` (curve, a compact text form) and ``shell_objects``
  (mesh n1 / n2 / max size, one-way distribution + span direction, uniform
  load set);
* inserts new tables (all editable, all round-trip byte-identically):
  spring / hinge / link properties, hinge backbones, response-spectrum,
  time-history and frequency functions with their point lists, frame
  curves, shell uniform load sets, tendons, nonlinear static cases (+
  loads) and steady-state / PSD cases (+ loads);
* chains rename / delete cascades for the references these features add.

The module is pure data transformation (no engine change).  Every new
model key a column writes is removed again when the value returns to its
default, so an untouched model -> rows -> apply is the identity.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import modeltables as MT
from .modeltables import (Col, RowError, Table, ObjectTable, _err, _rid,
                          _same, _names, _opt_names, _opt_stories,
                          parse_cell, _merge_ordered, _rename_keys,
                          _cascade_case, _case_refs)

num = "number"
G_DEF = "Model Definition > Properties"
G_FN = "Model Definition > Functions"
G_OBJ = "Model Definition > Objects"
G_LOAD = "Model Definition > Loads"
G_CASE = "Model Definition > Load Cases & Combinations"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def fmt_num(v) -> str:
    """Exact, short text for a float (repr; integral values without .0)."""
    v = float(v)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return repr(v)


def _put_default(key, default):
    """put(): store ``v`` or REMOVE the key when ``v`` is the default."""
    def put(obj, v, d):
        if v == default or v is None:
            obj.pop(key, None)
        else:
            obj[key] = v
    return put


def _chain(*fns):
    fns = [f for f in fns if f]

    def f(d, mp):
        for fn in fns:
            fn(d, mp)
    return f


def _chain_refs(*fns):
    fns = [f for f in fns if f]

    def f(d, gone):
        for fn in fns:
            msg = fn(d, gone)
            if msg:
                return msg
        return None
    return f


def _insert_after(T: List[Table], key: str, *tables: Table) -> None:
    idx = next(i for i, t in enumerate(T) if t.key == key)
    T[idx + 1:idx + 1] = list(tables)


def _table(T, key) -> Table:
    return next(t for t in T if t.key == key)


def _num_list_text(vals) -> str:
    return ", ".join(fmt_num(v) for v in vals)


def _parse_num_list(s) -> List[float]:
    if isinstance(s, (list, tuple)):
        return [float(v) for v in s]
    out = []
    for p in re.split(r"[,;\s]+", str(s or "").strip()):
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError:
            raise ValueError(f"{p!r} is not a number") from None
    return out


def _text_list(s) -> List[str]:
    if isinstance(s, (list, tuple)):
        return [str(v) for v in s]
    return [p.strip() for p in re.split(r"[,;]", str(s or "")) if p.strip()]


# --------------------------------------------------------------------------- #
# frame curve text form  ("via x,y,z" | "center x,y,z normal nx,ny,nz",
#  + optional "segments N" and "local2 normal")
# --------------------------------------------------------------------------- #
def curve_text(c) -> str:
    if not c:
        return ""
    parts = []
    if c.get("via") is not None:
        parts.append("via " + ",".join(fmt_num(v) for v in c["via"]))
    elif c.get("center") is not None:
        parts.append("center " + ",".join(fmt_num(v) for v in c["center"]))
        parts.append("normal " + ",".join(fmt_num(v)
                                          for v in c.get("plane_normal")
                                          or ()))
    if c.get("segments") is not None:
        parts.append(f"segments {int(c['segments'])}")
    if c.get("local2", "in_plane") != "in_plane":
        parts.append(f"local2 {c['local2']}")
    return " ".join(parts)


def parse_curve_text(s) -> Optional[dict]:
    s = (s or "").strip()
    if not s:
        return None
    toks = s.replace(";", " ").split()
    out: Dict[str, Any] = {"type": "arc"}
    i = 0

    def vec(t):
        try:
            v = [float(x) for x in t.split(",")]
        except ValueError:
            raise ValueError(f"{t!r} is not x,y,z") from None
        if len(v) != 3 or not all(math.isfinite(x) for x in v):
            raise ValueError(f"{t!r} is not x,y,z")
        return v
    while i < len(toks):
        k = toks[i].lower()
        if i + 1 >= len(toks):
            raise ValueError(f"{toks[i]!r} needs a value")
        v = toks[i + 1]
        if k == "via":
            out["via"] = vec(v)
        elif k == "center":
            out["center"] = vec(v)
        elif k == "normal":
            out["plane_normal"] = vec(v)
        elif k == "segments":
            if v.lower() == "auto":
                out["segments"] = None
            else:
                try:
                    out["segments"] = int(v)
                except ValueError:
                    raise ValueError("segments must be a whole number "
                                     "or auto") from None
        elif k == "local2":
            out["local2"] = v
        else:
            raise ValueError(f"unknown curve keyword {toks[i]!r} (use via, "
                             "center, normal, segments, local2)")
        i += 2
    if ("via" in out) == ("center" in out):
        raise ValueError('give "via x,y,z" or "center x,y,z normal nx,ny,nz"')
    out.setdefault("segments", None)
    out.setdefault("local2", "in_plane")
    return {k: out[k] for k in ("type", "via", "center", "plane_normal",
                                "segments", "local2") if k in out}


def _curve_get(o, d):
    return curve_text(o.get("curve"))


def _curve_put(o, v, d):
    c = parse_curve_text(v)
    if c is None:
        o.pop("curve", None)
    else:
        o["curve"] = c


# --------------------------------------------------------------------------- #
# list-of-entries child table (function points, case loads, backbones)
# --------------------------------------------------------------------------- #
class ListChildTable(Table):
    """Rows = the entries of a list held by each parent object.

    ``container`` is a dict of parents (``kind = "dict"``) or a list keyed
    by ``parent_key`` (``kind = "list"``).  ``sublists(p)`` yields
    ``(tag, list)`` pairs (one pair with tag ``""`` for a plain list field)
    and ``store(p, {tag: list})`` writes them back.  ``entry`` is
    ``"dict"`` (entries are objects), ``"pair"`` (``[x, y]``) or
    ``"scalar"``.  Row ``_id`` = ``[parent, tag, index]``.
    """

    container = ""
    kind = "dict"
    parent_key = "uid"
    parent_col = ""
    tag_col: Optional[str] = None
    field = ""
    entry = "dict"
    new_entry: Callable = staticmethod(lambda d: {})
    row_extra: Optional[Callable] = None     # (parent, tag, idx, e) -> dict

    def sublists(self, p):
        return [("", p.get(self.field) or [])]

    def store(self, p, by_tag):
        p[self.field] = by_tag.get("", [])

    def _parents(self, d):
        src = d.get(self.container) or ({} if self.kind == "dict" else [])
        if self.kind == "dict":
            return list(src.items())
        return [(o.get(self.parent_key), o) for o in src]

    def _wrap(self, e):
        if self.entry == "pair":
            return list(e)
        if self.entry == "scalar":
            return {"v": e}
        return copy.deepcopy(e)

    def _unwrap(self, e):
        if self.entry == "scalar":
            return e["v"]
        return e

    def rows(self, d):
        out = []
        for pk, p in self._parents(d):
            for tag, lst in self.sublists(p):
                for k, e in enumerate(lst):
                    r = {"_id": [pk, tag, k], self.parent_col: pk}
                    if self.tag_col:
                        r[self.tag_col] = tag
                    w = self._wrap(e)
                    for c in self.cols:
                        if c.key not in (self.parent_col, self.tag_col):
                            r[c.key] = c.get(w, d)
                    if self.row_extra:
                        r.update(self.row_extra(p, tag, k, e))
                    out.append(r)
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        parents = dict(self._parents(d))
        orig = {}
        for pk, p in parents.items():
            for tag, lst in self.sublists(p):
                for k, e in enumerate(lst):
                    orig[(pk, tag, k)] = e
        built: Dict[Any, Dict[str, list]] = {}
        seen = set()
        tcol = self.col(self.tag_col) if self.tag_col else None
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            pk = row.get(self.parent_col)
            pk = pk.strip() if isinstance(pk, str) else pk
            if pk not in parents:
                errs.append(_err(self.key, i, self.parent_col,
                                 f"unknown {self.col(self.parent_col).label}"
                                 f" {pk!r}"))
                continue
            tag = ""
            if tcol is not None:
                try:
                    tag = parse_cell(tcol, row.get(self.tag_col), d)
                except RowError as exc:
                    errs.append(_err(self.key, i, self.tag_col,
                                     f"{tcol.label}: {exc}"))
                    continue
            rid = _rid(row)
            rid = tuple(rid) if isinstance(rid, list) and len(rid) == 3 \
                else None
            if rid is not None and (rid not in orig or rid in seen):
                rid = None
            is_new = rid is None
            base = self._wrap(orig[rid]) if not is_new else \
                self._wrap(self.new_entry(d))
            self._row_values(i, row, base, d, errs, is_new,
                             skip=(self.parent_col, self.tag_col))
            if rid is not None:
                seen.add(rid)
            built.setdefault(pk, {}).setdefault(tag, []).append(
                self._unwrap(base))
        if len(errs) > n0:
            return {}
        changed = 0
        for pk, p in parents.items():
            old = {t: lst for t, lst in self.sublists(p)}
            new = built.get(pk, {})
            if all(t in new for t in old) and set(new) <= set(old) and \
                    all(_same_json(old[t], new[t]) for t in old):
                continue
            try:
                self.store(p, new)
            except ValueError as exc:
                errs.append(_err(self.key, None, self.tag_col, str(exc)))
                return {}
            changed += 1
        return {"rows": len(rows), "parents_changed": changed}


def _same_json(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_json(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same_json(x, y)
                                        for x, y in zip(a, b))
    return type(a) is type(b) and a == b


# --------------------------------------------------------------------------- #
# link parameters (per-link dict of scalars)
# --------------------------------------------------------------------------- #
class LinkParamTable(Table):
    """Rows = (link, parameter, value) for every NUMERIC scalar in
    ``links[i].params``.  Non-numeric parameters (lists, flags) are not
    rows and are always preserved."""

    def rows(self, d):
        out = []
        for lk in d.get("links") or []:
            for k, v in (lk.get("params") or {}).items():
                if MT._is_num(v):
                    out.append({"_id": [lk["uid"], k], "link": lk["uid"],
                                "param": k, "value": v})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        links = {lk["uid"]: lk for lk in d.get("links") or []}
        built: Dict[str, Dict[str, float]] = {}
        seen = set()
        pcol, vcol = self.col("param"), self.col("value")
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            u = row.get("link")
            u = u.strip() if isinstance(u, str) else u
            if u not in links:
                errs.append(_err(self.key, i, "link", f"unknown link {u!r}"))
                continue
            try:
                k = parse_cell(pcol, row.get("param"), d)
                if not k:
                    raise RowError("is required")
            except RowError as exc:
                errs.append(_err(self.key, i, "param", f"Parameter: {exc}"))
                continue
            try:
                v = parse_cell(vcol, row.get("value"), d)
            except RowError as exc:
                errs.append(_err(self.key, i, "value", f"Value: {exc}"))
                continue
            if (u, k) in seen:
                errs.append(_err(self.key, i, "param",
                                 f"duplicate parameter {k!r} on link {u!r}"))
                continue
            seen.add((u, k))
            built.setdefault(u, {})[k] = v
        if len(errs) > n0:
            return {}
        changed = 0
        for u, lk in links.items():
            old = lk.get("params") or {}
            keep = {k: v for k, v in old.items() if not MT._is_num(v)}
            nums = {k: v for k, v in old.items() if MT._is_num(v)}
            merged = _merge_ordered(nums, built.get(u, {}))
            new = {k: (keep[k] if k in keep else merged[k])
                   for k in old if k in keep or k in merged}
            for k, v in merged.items():
                if k not in new:
                    new[k] = v
            if _same_json(old, new):
                continue
            lk["params"] = new
            changed += 1
        return {"rows": len(rows), "links_changed": changed}


# --------------------------------------------------------------------------- #
# frame curves (members with a curve)
# --------------------------------------------------------------------------- #
class FrameCurveTable(Table):
    """Rows = the members that carry a ``curve``.  A row for a straight
    member makes it curved; a curved member without a row becomes
    straight."""

    def _vals(self, c):
        via = c.get("via")
        cen = c.get("center")
        p = via if via is not None else cen
        n = c.get("plane_normal") or [None, None, None]
        return {"form": "via" if via is not None else "center",
                "px": p[0], "py": p[1], "pz": p[2],
                "nx": n[0], "ny": n[1], "nz": n[2],
                "segments": c.get("segments"),
                "local2": c.get("local2", "in_plane")}

    def rows(self, d):
        out = []
        for m in d.get("members") or []:
            if m.get("curve"):
                out.append({"_id": m["uid"], "member": m["uid"],
                            **self._vals(m["curve"])})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        mem = {m["uid"]: m for m in d.get("members") or []}
        new: Dict[str, dict] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            u = row.get("member")
            u = u.strip() if isinstance(u, str) else u
            if u not in mem:
                errs.append(_err(self.key, i, "member",
                                 f"unknown frame {u!r}"))
                continue
            if u in new:
                errs.append(_err(self.key, i, "member",
                                 f"frame {u!r} is listed twice"))
                continue
            cur = mem[u].get("curve")
            vals = self._vals(cur) if cur else {
                "form": "via", "px": None, "py": None, "pz": None,
                "nx": None, "ny": None, "nz": None, "segments": None,
                "local2": "in_plane"}
            ok = True
            for c in self.cols:
                if c.key == "member" or c.key not in row:
                    continue
                try:
                    v = parse_cell(c, row[c.key], d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
                    ok = False
                    continue
                if not _same(v, vals.get(c.key)):
                    vals[c.key] = v
            if not ok:
                continue
            for k in ("px", "py", "pz"):
                if vals[k] is None:
                    errs.append(_err(self.key, i, k, f"{self.col(k).label} "
                                     "is required"))
                    ok = False
            if vals["form"] == "center" and None in (vals["nx"], vals["ny"],
                                                     vals["nz"]):
                errs.append(_err(self.key, i, "nx", "the center form needs "
                                 "the plane normal nx, ny, nz"))
                ok = False
            if not ok:
                continue
            c = {"type": "arc"}
            p = [vals["px"], vals["py"], vals["pz"]]
            if vals["form"] == "via":
                c["via"] = p
            else:
                c["center"] = p
                c["plane_normal"] = [vals["nx"], vals["ny"], vals["nz"]]
            c["segments"] = vals["segments"]
            c["local2"] = vals["local2"]
            new[u] = c if not (cur and _same_curve(cur, c)) else cur
        if len(errs) > n0:
            return {}
        changed = 0
        for u, m in mem.items():
            old = m.get("curve")
            nc = new.get(u)
            if nc is None:
                if old is not None:
                    m.pop("curve", None)
                    changed += 1
            elif nc is not old:
                m["curve"] = nc
                changed += 1
        return {"rows": len(rows), "members_changed": changed}


def _same_curve(a, b) -> bool:
    keys = set(a) | set(b)
    return all(_same(a.get(k), b.get(k)) for k in keys)


# --------------------------------------------------------------------------- #
# shell uniform load sets  (set, pattern, q, direction, projected)
# --------------------------------------------------------------------------- #
class LoadSetTable(Table):
    def rows(self, d):
        out = []
        for sn, s in (d.get("shell_load_sets") or {}).items():
            for p, e in s.items():
                e = e if isinstance(e, dict) else {"q": e}
                out.append({"_id": [sn, p], "set": sn, "pattern": p,
                            "q": e.get("q"),
                            "direction": e.get("direction", "gravity"),
                            "projected": bool(e.get("projected", False))})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        old = d.get("shell_load_sets") or {}
        built: Dict[str, Dict[str, Any]] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            vals = {}
            for c in self.cols:
                if c.key not in row:
                    if c.required:
                        errs.append(_err(self.key, i, c.key,
                                         f"{c.label} is required"))
                    continue
                try:
                    vals[c.key] = parse_cell(c, row[c.key], d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
            if len(errs) > n0:
                continue
            sn = vals.get("set") or ""
            if not sn:
                errs.append(_err(self.key, i, "set", "Load Set is required"))
                continue
            p = vals["pattern"]
            if p in built.get(sn, {}):
                errs.append(_err(self.key, i, "pattern",
                                 f"pattern {p!r} is listed twice in {sn!r}"))
                continue
            e: Dict[str, Any] = {"q": vals["q"]}
            if vals.get("direction", "gravity") != "gravity":
                e["direction"] = vals["direction"]
            if vals.get("projected"):
                e["projected"] = True
            prev = (old.get(sn) or {}).get(p)
            if isinstance(prev, dict) and _same_json_num(prev, e):
                e = prev
            elif MT._is_num(prev) and set(e) == {"q"} and _same(prev, e["q"]):
                e = prev
            built.setdefault(sn, {})[p] = e
        if len(errs) > n0:
            return {}
        new = _merge_ordered(old, built)
        new = {k: (_merge_ordered(old[k], v) if k in old else v)
               for k, v in new.items()}
        if not _same_json(old, new):
            if new:
                d["shell_load_sets"] = new
            else:
                d.pop("shell_load_sets", None)
        return {"rows": len(rows), "sets": len(new)}


def _same_json_num(a, b) -> bool:
    return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)


# --------------------------------------------------------------------------- #
# cascades for the new references
# --------------------------------------------------------------------------- #
def _cascade_story_ext(d, mp):
    for s in d.get("stories") or []:
        if s.get("similar_to") in mp:
            s["similar_to"] = mp[s["similar_to"]]
    for c in (d.get("nonlinear_static_cases") or {}).values():
        if c.get("control_story") in mp:
            c["control_story"] = mp[c["control_story"]]


def _delete_story_ext(d, gone):
    for s in d.get("stories") or []:
        if s.get("similar_to") in gone:
            s.pop("similar_to", None)


def _cascade_pattern_ext(d, mp):
    if isinstance(d.get("shell_load_sets"), dict):
        d["shell_load_sets"] = {k: _rename_keys(v, mp) for k, v in
                                d["shell_load_sets"].items()}
    for k in ("nonlinear_static_cases", "steady_state_cases", "psd_cases"):
        for c in (d.get(k) or {}).values():
            for ld in c.get("loads") or []:
                if ld.get("pattern") in mp:
                    ld["pattern"] = mp[ld["pattern"]]
    for t in d.get("tendons") or []:
        if t.get("pattern") in mp:
            t["pattern"] = mp[t["pattern"]]


def _pattern_refs_ext(d, gone):
    for sn, s in (d.get("shell_load_sets") or {}).items():
        for p in s:
            if p in gone:
                return f"load pattern {p!r} is used by shell load set {sn!r}"
    for k in ("nonlinear_static_cases", "steady_state_cases", "psd_cases"):
        for cn, c in (d.get(k) or {}).items():
            for ld in c.get("loads") or []:
                if ld.get("pattern") in gone:
                    return (f"load pattern {ld['pattern']!r} is used by "
                            f"load case {cn!r}")
    for t in d.get("tendons") or []:
        if t.get("pattern") in gone:
            return f"load pattern {t['pattern']!r} is used by tendon " \
                   f"{t.get('uid')!r}"
    return None


def _cascade_host(d, mp):
    for t in d.get("tendons") or []:
        h = t.get("host")
        if isinstance(h, list):
            t["host"] = [mp.get(u, u) for u in h]


def _delete_host(d, gone):
    for t in d.get("tendons") or []:
        h = t.get("host")
        if isinstance(h, list) and any(u in gone for u in h):
            t["host"] = [u for u in h if u not in gone]


def _cascade_nls(d, mp):
    for c in (d.get("nonlinear_static_cases") or {}).values():
        if c.get("start_from") in mp:
            c["start_from"] = mp[c["start_from"]]
    for c in (d.get("pushover_cases") or {}).values():
        if c.get("start_from") in mp:
            c["start_from"] = mp[c["start_from"]]


def _cascade_spring_prop(d, mp):
    for s in d.get("spring_supports") or []:
        if s.get("property") in mp:
            s["property"] = mp[s["property"]]


def _spring_prop_refs(d, gone):
    for s in d.get("spring_supports") or []:
        if s.get("property") in gone:
            return (f"spring property {s['property']!r} is used by the "
                    f"spring at {s.get('point')}")
    return None


def _cascade_hinge_prop(d, mp):
    for m in d.get("members") or []:
        if isinstance(m.get("hinges"), list):
            for h in m["hinges"]:
                if isinstance(h, dict) and h.get("property") in mp:
                    h["property"] = mp[h["property"]]


def _hinge_prop_refs(d, gone):
    for m in d.get("members") or []:
        for h in m.get("hinges") if isinstance(m.get("hinges"), list) else []:
            if isinstance(h, dict) and h.get("property") in gone:
                return (f"hinge property {h['property']!r} is used by frame "
                        f"{m['uid']!r}")
    return None


def _fn_cascade(case_keys):
    def f(d, mp):
        for k in case_keys:
            for c in (d.get(k) or {}).values():
                if c.get("function") in mp:
                    c["function"] = mp[c["function"]]
                for ld in c.get("loads") or []:
                    if isinstance(ld, dict) and ld.get("function") in mp:
                        ld["function"] = mp[ld["function"]]
    return f


def _fn_refs(case_keys):
    def f(d, gone):
        for k in case_keys:
            for cn, c in (d.get(k) or {}).items():
                fns = [c.get("function")] + [ld.get("function") for ld in
                                             c.get("loads") or []
                                             if isinstance(ld, dict)]
                for fn in fns:
                    if fn and fn in gone:
                        return f"function {fn!r} is used by load case {cn!r}"
        return None
    return f


# --------------------------------------------------------------------------- #
# appended columns
# --------------------------------------------------------------------------- #
def _md_get(k):
    def g(o, d):
        md = o.get("mesh_divisions")
        return md.get(k) if isinstance(md, dict) else None
    return g


def _md_put(k):
    def p(o, v, d):
        md = dict(o.get("mesh_divisions") or {})
        if k == "max_size":
            md = {"max_size": v} if v is not None else \
                {kk: vv for kk, vv in md.items() if kk != "max_size"}
        elif v is None:
            md.pop(k, None)
        else:
            md.pop("max_size", None)
            md[k] = v
        if md:
            o["mesh_divisions"] = md
        else:
            o.pop("mesh_divisions", None)
    return p


class StorySimilarTable(Table):
    """Story Data - Master / Similar: one row per story (the story list
    itself is edited in ``stories``, whose legacy columns are unchanged)."""

    can_add = False
    can_delete = False
    keycol = "story"

    def rows(self, d):
        return [{"_id": s["name"], "story": s["name"],
                 "master": bool(s.get("master", False)),
                 "similar_to": s.get("similar_to") or ""}
                for s in d.get("stories") or []]

    def apply(self, d, rows, errs):
        n0 = len(errs)
        by = {s["name"]: s for s in d.get("stories") or []}
        vals = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            st = row.get("story", _rid(row))
            st = st.strip() if isinstance(st, str) else st
            if st not in by:
                errs.append(_err(self.key, i, "story",
                                 f"unknown story {st!r}"))
                continue
            if st in vals:
                errs.append(_err(self.key, i, "story",
                                 f"story {st!r} is listed twice"))
                continue
            v = {}
            for c in self.cols[1:]:
                if c.key not in row:
                    continue
                try:
                    v[c.key] = parse_cell(c, row[c.key], d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
            vals[st] = v
        if len(errs) > n0:
            return {}
        changed = 0
        for st, v in vals.items():
            s = by[st]
            if "master" in v and bool(s.get("master", False)) != v["master"]:
                if v["master"]:
                    s["master"] = True
                else:
                    s.pop("master", None)
                changed += 1
            if "similar_to" in v and (s.get("similar_to") or "") != \
                    v["similar_to"]:
                if v["similar_to"]:
                    s["similar_to"] = v["similar_to"]
                else:
                    s.pop("similar_to", None)
                changed += 1
        return {"rows": len(rows), "changed": changed}


def _extend_columns(T: List[Table]) -> None:
    from .shell_types import SHELL_TYPES, DISTRIBUTIONS
    st = _table(T, "stories")
    st.on_rename = _chain(st.on_rename, _cascade_story_ext)
    st.on_delete = _chain(st.on_delete, _delete_story_ext)
    ss = _table(T, "shell_sections")
    ss.cols = ss.cols + [
        Col("shell_type", "Shell Type", "enum", enum=SHELL_TYPES,
            default="shell", get=lambda o, d: o.get("shell_type") or "shell",
            put=_put_default("shell_type", "shell")),
    ]
    fo = _table(T, "frame_objects")
    i = next(k for k, c in enumerate(fo.cols) if c.key == "length")
    fo.cols = fo.cols[:i] + [
        Col("curve", "Curve", "text", default="", get=_curve_get,
            put=_curve_put)] + fo.cols[i:]
    fo.on_rename = _chain(fo.on_rename, _cascade_host)
    fo.on_delete = _chain(fo.on_delete, _delete_host)
    so = _table(T, "shell_objects")
    i = next(k for k, c in enumerate(so.cols) if c.key == "area")
    so.cols = so.cols[:i] + [
        Col("mesh_n1", "Mesh n1", "int", optional=True,
            get=_md_get("n1"), put=_md_put("n1")),
        Col("mesh_n2", "Mesh n2", "int", optional=True,
            get=_md_get("n2"), put=_md_put("n2")),
        Col("mesh_max", "Mesh Max Size", num, "length", optional=True,
            get=_md_get("max_size"), put=_md_put("max_size")),
        Col("distribution", "Load Distribution", "enum", enum=DISTRIBUTIONS,
            default="two_way",
            get=lambda o, d: o.get("distribution") or "two_way",
            put=_put_default("distribution", "two_way")),
        Col("one_way_dir", "Span Direction", "int", enum=(1, 2), default=1,
            get=lambda o, d: o.get("one_way_dir", 1),
            put=_put_default("one_way_dir", 1)),
        Col("load_set", "Uniform Load Set", "text", default="",
            get=lambda o, d: o.get("load_set") or "",
            put=_put_default("load_set", "")),
    ] + so.cols[i:]
    so.on_rename = _chain(so.on_rename, _cascade_host)
    so.on_delete = _chain(so.on_delete, _delete_host)
    lp = _table(T, "load_patterns")
    lp.on_rename = _chain(lp.on_rename, _cascade_pattern_ext)
    lp.delete_refs = _chain_refs(lp.delete_refs, _pattern_refs_ext)


# --------------------------------------------------------------------------- #
# new tables
# --------------------------------------------------------------------------- #
def _k_cols():
    out = []
    for i, lb in enumerate(("k1", "k2", "k3", "kr1", "kr2", "kr3")):
        def put(o, v, d, i=i):
            k = list(o.get("k") or [0.0] * 6)
            k[i] = v
            o["k"] = k
        out.append(Col(lb, lb.upper(), num,
                       "stiffness" if i < 3 else "rot_stiffness",
                       path=("k", i), default=0.0, put=put))
    return out


def _spring_angle_get(o, d):
    la = o.get("local_axes")
    if isinstance(la, dict):
        return la.get("angle_deg", 0.0)
    return 0.0 if la is None else None


def _spring_angle_put(o, v, d):
    if v is None:
        return
    if v == 0:
        if isinstance(o.get("local_axes"), dict):
            o.pop("local_axes", None)
    else:
        o["local_axes"] = {"angle_deg": v}


def _yd_get(o, d):
    v = (o.get("scale") or {}).get("yield_deformation")
    return "" if v is None else (v if isinstance(v, str) else fmt_num(v))


def _yd_put(o, v, d):
    sc = dict(o.get("scale") or {"yield_value": 1.0})
    s = (v or "").strip()
    if not s:
        sc["yield_deformation"] = None
    elif s.lower() == "auto":
        sc["yield_deformation"] = "auto"
    else:
        try:
            sc["yield_deformation"] = float(s)
        except ValueError:
            raise ValueError("Yield Deformation SF: a number, auto or "
                             "blank") from None
    o["scale"] = sc


def _sc_get(o, d):
    return (o.get("scale") or {}).get("yield_value", 1.0)


def _sc_put(o, v, d):
    sc = dict(o.get("scale") or {"yield_deformation": None})
    sc["yield_value"] = v
    o["scale"] = {"yield_value": sc["yield_value"],
                  "yield_deformation": sc.get("yield_deformation")}


def _acc_col(k):
    def g(o, d):
        return (o.get("acceptance") or {}).get(k)

    def p(o, v, d):
        acc = dict(o.get("acceptance") or {})
        if v is None:
            acc.pop(k, None)
        else:
            acc[k] = v
        o["acceptance"] = acc or None
        if not acc:
            o.pop("acceptance", None)
    return Col(k, f"Acceptance {k}", num, optional=True, get=g, put=p)


class BackboneTable(ListChildTable):
    def sublists(self, p):
        bb = p.get("backbone")
        if isinstance(bb, dict):
            return [("positive", bb.get("positive") or []),
                    ("negative", bb.get("negative") or [])]
        return [("both", bb or [])]

    def store(self, p, by_tag):
        if set(by_tag) <= {"both"}:
            p["backbone"] = by_tag.get("both", [])
            return
        if "both" in by_tag:
            raise ValueError("a backbone is either symmetric (side both) or "
                             "positive + negative, not both")
        p["backbone"] = {"positive": by_tag.get("positive", []),
                         "negative": by_tag.get("negative", [])}


def _opt_none_col(key, label, enum, **kw):
    return Col(key, label, "enum", enum=enum, default="",
               get=lambda o, d: o.get(key) or "",
               put=lambda o, v, d: o.__setitem__(key, v or None), **kw)


def _fn_count(field):
    return lambda o, d: len(o.get(field) or [])


def _th_time(p, tag, k, e):
    return {"t": k * float(p.get("dt") or 0.0)}


def _loss_col(k, label, q="none"):
    def g(o, d):
        return (o.get("losses") or {}).get(k, 0.0)

    def p(o, v, d):
        ls = dict(o.get("losses") or {})
        ls[k] = v
        o["losses"] = ls
    return Col(k, label, num, q, default=0.0, get=g, put=p)


def _host_put(o, v, d):
    o["host"] = _text_list(v)


def _sags_put(o, v, d):
    try:
        o["sags"] = _parse_num_list(v)
    except ValueError as exc:
        raise ValueError(str(exc)) from None


def _tendon_material_put(o, v, d):
    o["material"] = v


def _freq_load_patterns(d):
    return list((d.get("patterns") or {}).keys()) + ["accel"]


_freq_load_patterns.refc = "patterns"


def _new_tables(T: List[Table]) -> None:
    from .springprops import SPRING_KINDS, DOF_LABELS
    from .user_hinges import HINGE_TYPES, HYSTERESIS_TYPES, DROP_OPTIONS
    from .nonlinear_static import (NLS_LOAD_APPLICATIONS, NLS_GEOMETRIC,
                                   NLS_DOFS, NLS_HINGE_MODES)
    from .frequency_cases import (FREQ_FUNCTION_KINDS, FREQ_ACCEL_DIRECTIONS,
                                  FREQ_DAMPING_TYPES, FREQ_METHODS)
    from .tendons import JACKING_ENDS
    from .curved import LOCAL2_OPTIONS
    from .loads_ext import AREA_LOAD_DIRECTIONS

    # ---- properties: springs, hinges, link parameters
    springs = ObjectTable(
        "spring_properties", "Spring Properties - Point", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("kind", "Kind", "enum", enum=SPRING_KINDS, default="linear"),
            *_k_cols(),
            Col("nonlinear_dof", "Nonlinear DOF", "enum",
                enum=("",) + DOF_LABELS, default="",
                get=lambda o, d: o.get("nonlinear_dof") or "",
                put=_put_default("nonlinear_dof", "")),
            Col("gap", "Gap", num, "length", optional=True,
                put=_put_default("gap", None)),
            Col("angle_deg", "Local Axis Angle", num, "angle_deg",
                optional=True, get=_spring_angle_get, put=_spring_angle_put),
        ], container="spring_properties", keycol="name", name_in_obj=False,
        on_rename=_cascade_spring_prop, delete_refs=_spring_prop_refs,
        new_obj=lambda d: {"kind": "linear", "k": [0.0] * 6})
    hinges = ObjectTable(
        "hinge_properties", "Frame Hinge Properties", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("type", "Type", "enum", enum=HINGE_TYPES, default="M3"),
            Col("hysteresis", "Hysteresis", "enum", enum=HYSTERESIS_TYPES,
                default="kinematic"),
            Col("drop_strength", "After Point E", "enum", enum=DROP_OPTIONS,
                default="drops"),
            Col("yield_value", "Force SF", num, default=1.0, get=_sc_get,
                put=_sc_put),
            Col("yield_deformation", "Deformation SF", "text", default="",
                get=_yd_get, put=_yd_put),
            _acc_col("IO"), _acc_col("LS"), _acc_col("CP"),
            Col("k_elastic", "Elastic Stiffness", num, optional=True),
            Col("n_points", "Points", "int", editable=False,
                get=lambda o, d: (len(o.get("backbone") or [])
                                  if isinstance(o.get("backbone"), list)
                                  else len((o.get("backbone") or {})
                                           .get("positive") or []))),
        ], container="hinge_properties", keycol="name", name_in_obj=False,
        on_rename=_cascade_hinge_prop, delete_refs=_hinge_prop_refs,
        new_obj=lambda d: {"type": "M3", "backbone": [],
                           "scale": {"yield_value": 1.0,
                                     "yield_deformation": None},
                           "hysteresis": "kinematic",
                           "drop_strength": "drops", "k_elastic": None,
                           "hysteresis_params": None})
    backbone = BackboneTable(
        "hinge_backbones", "Frame Hinge Properties - Backbone Points", G_DEF, [
            Col("hinge", "Hinge Property", "enum",
                enum=_names("hinge_properties"), required=True),
            Col("side", "Side", "enum", enum=("both", "positive", "negative"),
                default="both"),
            Col("d", "Plastic Deformation", num, path=(0,), required=True),
            Col("f", "Force / Moment", num, path=(1,), required=True),
        ], container="hinge_properties", parent_col="hinge", tag_col="side",
        entry="pair", new_entry=lambda d: [0.0, 0.0])
    _insert_after(T, "shell_sections", springs, hinges, backbone)
    linkp = LinkParamTable(
        "link_properties", "Link Properties - Parameters", G_OBJ, [
            Col("link", "Link", "ref", ref="links", required=True),
            Col("param", "Parameter", "text", required=True),
            Col("value", "Value", num, required=True),
        ])

    # ---- functions
    rs_fn = ObjectTable(
        "rs_functions", "Functions - Response Spectrum", G_FN, [
            Col("name", "Name", "text", required=True),
            Col("damping", "Damping", num, default=0.05),
            Col("n_points", "Points", "int", editable=False,
                get=_fn_count("points")),
        ], container="spectrum_functions", keycol="name",
        on_rename=_fn_cascade(("rs_cases",)),
        delete_refs=_fn_refs(("rs_cases",)),
        new_obj=lambda d: {"points": [], "damping": 0.05})
    rs_pts = ListChildTable(
        "rs_function_points", "Functions - Response Spectrum - Points", G_FN, [
            Col("function", "Function", "enum",
                enum=_names("spectrum_functions"), required=True),
            Col("period", "Period", num, "period", path=(0,), required=True),
            Col("value", "Value", num, path=(1,), required=True),
        ], container="spectrum_functions", parent_col="function",
        field="points", entry="pair", new_entry=lambda d: [0.0, 0.0])
    th_fn = ObjectTable(
        "th_functions", "Functions - Time History", G_FN, [
            Col("name", "Name", "text", required=True),
            Col("dt", "Time Step", num, "period", required=True),
            Col("n_values", "Values", "int", editable=False,
                get=_fn_count("values")),
        ], container="th_functions", keycol="name",
        on_rename=_fn_cascade(("th_cases",)),
        delete_refs=_fn_refs(("th_cases",)),
        new_obj=lambda d: {"values": [], "dt": 0.01})
    th_vals = ListChildTable(
        "th_function_values", "Functions - Time History - Values", G_FN, [
            Col("function", "Function", "enum", enum=_names("th_functions"),
                required=True),
            Col("t", "Time", num, "period", editable=False),
            Col("value", "Acceleration", num, path=("v",), required=True),
        ], container="th_functions", parent_col="function", field="values",
        entry="scalar", new_entry=lambda d: 0.0, row_extra=_th_time)
    fq_fn = ObjectTable(
        "frequency_functions", "Functions - Frequency (Steady State / PSD)",
        G_FN, [
            Col("name", "Name", "text", required=True),
            Col("kind", "Kind", "enum", enum=FREQ_FUNCTION_KINDS,
                default="steady_state"),
            Col("n_points", "Points", "int", editable=False,
                get=_fn_count("points")),
        ], container="frequency_functions", keycol="name",
        on_rename=_fn_cascade(("steady_state_cases", "psd_cases")),
        delete_refs=_fn_refs(("steady_state_cases", "psd_cases")),
        new_obj=lambda d: {"kind": "steady_state", "points": []})
    fq_pts = ListChildTable(
        "frequency_function_points", "Functions - Frequency - Points", G_FN, [
            Col("function", "Function", "enum",
                enum=_names("frequency_functions"), required=True),
            Col("f", "Frequency (Hz)", num, path=(0,), required=True),
            Col("value", "Value", num, path=(1,), required=True),
        ], container="frequency_functions", parent_col="function",
        field="points", entry="pair", new_entry=lambda d: [0.0, 0.0])
    _insert_after(T, "hinge_backbones", rs_fn, rs_pts, th_fn, th_vals,
                  fq_fn, fq_pts)

    # ---- objects: link parameters + frame curves
    curves = FrameCurveTable(
        "frame_curves", "Frame Objects - Curved Geometry", G_OBJ, [
            Col("member", "Frame", "ref", ref="members", required=True),
            Col("form", "Form", "enum", enum=("via", "center"),
                default="via"),
            Col("px", "Point X (via / center)", num, "length",
                required=True),
            Col("py", "Point Y", num, "length", required=True),
            Col("pz", "Point Z", num, "length", required=True),
            Col("nx", "Normal X", num, optional=True),
            Col("ny", "Normal Y", num, optional=True),
            Col("nz", "Normal Z", num, optional=True),
            Col("segments", "Segments (blank = auto)", "int", optional=True),
            Col("local2", "Local 2 Axis", "enum", enum=LOCAL2_OPTIONS,
                default="in_plane"),
        ])
    _insert_after(T, "links", linkp, curves)
    sim_story = Col("story", "Story", "text", editable=False)
    sim_story.refc = "stories"
    _insert_after(T, "stories", StorySimilarTable(
        "story_similarity", "Story Data - Master / Similar Stories", G_DEF, [
            sim_story,
            Col("master", "Master Story", "bool", default=False),
            Col("similar_to", "Similar To", "enum", enum=_opt_stories,
                default=""),
        ]))

    # ---- loads: shell load sets, tendons
    lsets = LoadSetTable(
        "shell_load_sets", "Shell Uniform Load Sets", G_LOAD, [
            Col("set", "Load Set", "text", required=True),
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("q", "Load", num, "pressure", required=True),
            Col("direction", "Direction", "enum", enum=AREA_LOAD_DIRECTIONS,
                default="gravity"),
            Col("projected", "Projected", "bool", default=False),
        ])
    tendons = ObjectTable(
        "tendons", "Tendons (as loads)", G_LOAD, [
            Col("uid", "Tendon", "text"),
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("host", "Host Objects", "text", required=True,
                get=lambda o, d: ", ".join(o.get("host") or []),
                put=_host_put),
            Col("points", "Profile Points", "points", "length",
                required=True),
            Col("sags", "Segment Sags", "text", default="",
                get=lambda o, d: _num_list_text(o.get("sags") or []),
                put=_sags_put),
            Col("material", "Material", "text", default="A416Gr270",
                put=_tendon_material_put),
            Col("area", "Area", num, "area", required=True),
            Col("jacking_stress", "Jacking Stress", num, "stress",
                required=True),
            Col("jacking_end", "Jacking End", "enum", enum=JACKING_ENDS,
                default="start"),
            _loss_col("friction_mu", "Friction μ"),
            _loss_col("wobble_k", "Wobble k"),
            _loss_col("anchor_set", "Anchor Set", "length"),
            _loss_col("long_term_fraction", "Long-term Loss"),
            Col("n_sub", "Sub-pieces", "int", default=16),
        ], container="tendons", kind="list", keycol="uid", auto_prefix="T",
        new_obj=lambda d: {"uid": "", "sags": [], "host": []})
    _insert_after(T, "area_loads", lsets, tendons)

    # ---- cases: nonlinear static, steady state, PSD
    nls_case_names = _names("nonlinear_static_cases")
    nls = ObjectTable(
        "nonlinear_static_cases", "Load Cases - Nonlinear Static", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("load_application", "Load Application", "enum",
                enum=NLS_LOAD_APPLICATIONS, default="load_control"),
            Col("steps", "Steps", "int", default=10),
            _opt_none_col("control_story", "Control Story", _opt_stories),
            Col("control_dof", "Control DOF", "enum", enum=NLS_DOFS,
                default="UX"),
            Col("target_disp", "Target Displacement", num, "length",
                optional=True),
            Col("geometric", "Geometric Nonlinearity", "enum",
                enum=NLS_GEOMETRIC, default="none"),
            _opt_none_col("start_from", "Start From",
                          _opt_names("nonlinear_static_cases")),
            Col("hinges", "Hinges", "enum", enum=NLS_HINGE_MODES,
                default="column_base"),
            Col("hardening", "Hardening", num, default=0.02),
        ], container="nonlinear_static_cases", keycol="name",
        on_rename=_chain(_cascade_case, _cascade_nls),
        delete_refs=_case_refs,
        new_obj=lambda d: {"loads": [], "load_application": "load_control",
                           "steps": 10, "control_point": None,
                           "control_story": None, "control_dof": "UX",
                           "target_disp": None, "geometric": "none",
                           "start_from": None, "hinges": "column_base",
                           "My": {}, "default_My": None, "hardening": 0.02,
                           "hinge_params": {}})
    nls_loads = ListChildTable(
        "nonlinear_static_loads", "Load Cases - Nonlinear Static - Loads",
        G_CASE, [
            Col("case", "Case", "enum", enum=nls_case_names, required=True),
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("scale", "Scale Factor", num, required=True),
        ], container="nonlinear_static_cases", parent_col="case",
        field="loads", entry="dict",
        new_entry=lambda d: {"pattern": "", "scale": 1.0})

    def freq_case(key, title, container):
        return ObjectTable(
            key, title, G_CASE, [
                Col("name", "Case", "text", required=True),
                Col("freq_start_hz", "Start Frequency (Hz)", num,
                    default=0.0),
                Col("freq_end_hz", "End Frequency (Hz)", num, default=10.0),
                Col("n_freq", "Frequencies", "int", default=101),
                Col("modal_refine", "Modal Refinement", "bool", default=True),
                Col("method", "Method", "enum", enum=FREQ_METHODS,
                    default="modal"),
                Col("num_modes", "Modes (0 = model)", "int", default=0),
                Col("damping", "Damping", num, default=0.05),
                Col("damping_type", "Damping Type", "enum",
                    enum=FREQ_DAMPING_TYPES, default="modal"),
            ], container=container, keycol="name", on_rename=_cascade_case,
            delete_refs=_case_refs,
            new_obj=lambda d: {"loads": [], "freq_start_hz": 0.0,
                               "freq_end_hz": 10.0, "n_freq": 101,
                               "frequencies": [], "modal_refine": True,
                               "method": "modal", "num_modes": 0,
                               "damping": 0.05, "damping_type": "modal",
                               "output_points": []})

    def freq_loads(key, title, container):
        return ListChildTable(
            key, title, G_CASE, [
                Col("case", "Case", "enum", enum=_names(container),
                    required=True),
                Col("pattern", "Load Pattern / accel", "enum",
                    enum=_freq_load_patterns, required=True),
                Col("direction", "Accel Direction", "enum",
                    enum=("",) + FREQ_ACCEL_DIRECTIONS, default=""),
                Col("scale", "Scale Factor", num, default=1.0),
                Col("function", "Function", "enum",
                    enum=_opt_names("frequency_functions"), default=""),
                Col("phase_deg", "Phase", num, "angle_deg", default=0.0),
            ], container=container, parent_col="case", field="loads",
            entry="dict",
            new_entry=lambda d: {"pattern": "", "direction": "",
                                 "scale": 1.0, "function": "",
                                 "phase_deg": 0.0})
    _insert_after(T, "pushover_cases", nls, nls_loads,
                  freq_case("steady_state_cases",
                            "Load Cases - Steady State",
                            "steady_state_cases"),
                  freq_loads("steady_state_loads",
                             "Load Cases - Steady State - Loads",
                             "steady_state_cases"),
                  freq_case("psd_cases",
                            "Load Cases - Power Spectral Density",
                            "psd_cases"),
                  freq_loads("psd_loads",
                             "Load Cases - Power Spectral Density - Loads",
                             "psd_cases"))


def extend_catalogue(T: List[Table]) -> None:
    """Hook called once by ``modeltables._build_catalogue``."""
    _extend_columns(T)
    _new_tables(T)
