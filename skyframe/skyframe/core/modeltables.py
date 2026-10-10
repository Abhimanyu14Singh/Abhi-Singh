"""ETABS "Interactive Database Editing": the MODEL definition as editable tables.

Pure model-data transformation (no analysis).  Every table is a view of the
model's JSON form (``BuildingModel.to_dict()``) and can be written back:

    list_tables(d)                    -> catalogue [{key, title, group, ...}]
    get_table(d, key)                 -> {key, title, group, editable,
                                          can_add, can_delete, columns, rows}
    apply_tables(d, {key: rows})      -> (BuildingModel | None, errors, summary)
    table_to_csv(d, key) / csv_to_rows(d, key, text)
    export_zip(d) / import_zip(d, data)

Rows are dicts keyed by column ``key`` plus a hidden ``"_id"`` that names the
model object the row came from (dict tables: the original name; list tables:
the original list index; per-pattern load tables: ``[pattern, list, index]``).
On apply a row WITH ``_id`` edits that object (fields that are not table
columns are preserved untouched; a cell whose parsed value equals the stored
value is not written at all, so model -> rows -> apply is the identity), a
row WITHOUT ``_id`` matching an existing key (name / uid) edits that object,
any other row ADDS an object, and objects without a row are DELETED.

Apply is ATOMIC: every row of every table is parsed and checked first
(type / enum / reference / required / duplicate errors are reported per
``{table, row, col, message}``); only when no row error exists is the edited
dict rebuilt through ``BuildingModel.from_dict`` (the normal model
validation).  A model-level validation failure is reported as one error,
attributed to the row whose identifier the message names when possible.  On
any error nothing is applied.

Renaming a named definition cascades to its references (material ->
sections; frame / shell section -> objects; pattern -> cases / mass source;
case / combo -> combos; story -> objects; uid -> loads / groups; group ->
section cuts; diaphragm -> shells / joints).  Deleting a frame / shell /
link object also deletes its loads and group memberships (ETABS behaviour);
deleting any other referenced definition is reported as an error.

Every numeric column carries a ``quantity`` naming a js/units.js kind (the
model stays SI; see :data:`QUANTITIES` for the SI unit of each).
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
import zipfile
from typing import Any, Callable, Dict, List, Optional, Tuple

# quantity (js/units.js kind) -> SI unit of the stored value
QUANTITIES: Dict[str, str] = {
    "none": "",
    "length": "m",
    "dim": "m",
    "area": "m^2",
    "inertia": "m^4",
    "force": "kN",
    "moment": "kN*m",
    "line_force": "kN/m",
    "pressure": "kPa",
    "stress": "kPa",
    "modulus": "kPa",
    "unit_weight": "kN/m^3",
    "mass": "tonne",
    "mass_density": "tonne/m^3",
    "mass_per_length": "tonne/m",
    "mass_per_area": "tonne/m^2",
    "stiffness": "kN/m",
    "rot_stiffness": "kN*m/rad",
    "line_spring": "kN/m/m",
    "thermal_coeff": "1/degC",
    "period": "s",
    "angle_deg": "deg",
}

COL_TYPES = ("number", "int", "bool", "enum", "text", "ref", "points")

PATTERN_KINDS = ("dead", "live", "quake", "wind", "other", "notional",
                 "prestress")
DIST_KINDS = ("udl", "trapezoid")


class RowError(Exception):
    """A per-cell error raised while parsing a row."""


# --------------------------------------------------------------------------- #
# columns
# --------------------------------------------------------------------------- #
class Col:
    """One table column.

    ``path`` addresses the stored value inside the object dict (a key or a
    tuple of keys / indices); ``get`` / ``put`` override it.  ``enum`` is a
    static tuple or a callable ``f(d) -> list`` (dynamic references).
    """

    def __init__(self, key, label, type_="number", quantity="none",
                 path=None, enum=None, editable=True, required=False,
                 optional=False, default=None, get=None, put=None, ref=None):
        assert type_ in COL_TYPES, type_
        assert quantity in QUANTITIES, quantity
        self.key, self.label, self.type, self.quantity = key, label, type_, \
            quantity
        self.path = (path if isinstance(path, tuple)
                     else ((path or key),))
        self.enum, self.editable, self.required = enum, editable, required
        self.optional, self.default = optional, default
        self._get, self._put, self.ref = get, put, ref
        # container whose names this column references (rename tracking)
        self.refc = ref or getattr(enum, "refc", None)

    def options(self, d):
        if self.enum is None:
            return None
        return list(self.enum(d)) if callable(self.enum) else list(self.enum)

    def meta(self, d) -> dict:
        out = {"key": self.key, "label": self.label, "quantity": self.quantity,
               "unit": QUANTITIES[self.quantity], "type": self.type,
               "editable": self.editable}
        opts = self.options(d)
        if opts is not None:
            out["enum"] = opts
        if self.ref:
            out["ref"] = self.ref
        if self.required:
            out["required"] = True
        if self.optional:
            out["optional"] = True
        if self.default is not None and self.editable:
            out["default"] = self.default
        return out

    def get(self, obj, d=None):
        if self._get:
            return self._get(obj, d)
        cur = obj
        for p in self.path:
            try:
                cur = cur[p]
            except (KeyError, IndexError, TypeError):
                return self.default
        return self.default if cur is None and not self.optional else cur

    def put(self, obj, value, d=None):
        if self._put:
            self._put(obj, value, d)
            return
        cur = obj
        for p in self.path[:-1]:
            cur = cur[p]
        cur[self.path[-1]] = value


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_TRUE = {"true", "yes", "y", "1", "t", "on", "x"}
_FALSE = {"false", "no", "n", "0", "f", "off", ""}


def parse_points(raw) -> List[List[float]]:
    """``[[x,y,z], ...]`` or ``"x,y,z; x,y,z"`` -> list of 3-float lists."""
    if isinstance(raw, str):
        pts = []
        for chunk in raw.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = [p for p in re.split(r"[,\s]+", chunk) if p]
            pts.append(parts)
        raw = pts
    if not isinstance(raw, (list, tuple)):
        raise RowError("expected a list of points 'x,y,z; x,y,z; ...'")
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 3:
            raise RowError("every point needs exactly 3 coordinates x,y,z")
        try:
            q = [float(v) for v in p]
        except (TypeError, ValueError):
            raise RowError("point coordinates must be numbers") from None
        if not all(math.isfinite(v) for v in q) or any(
                isinstance(v, bool) for v in p):
            raise RowError("point coordinates must be finite numbers")
        out.append(q)
    return out


def parse_cell(col: Col, raw, d) -> Any:
    """Coerce one raw cell value (JSON / CSV text) to the column type.
    Raises :class:`RowError` with a user-facing message."""
    t = col.type
    blank = raw is None or (isinstance(raw, str) and not raw.strip())
    if t in ("number", "int"):
        if blank:
            if col.optional:
                return None
            raise RowError("a number is required")
        if isinstance(raw, bool):
            raise RowError("must be a number")
        if isinstance(raw, str):
            try:
                raw = float(raw.strip().replace("−", "-"))
            except ValueError:
                raise RowError(f"{raw!r} is not a number") from None
        if not _is_num(raw) or not math.isfinite(float(raw)):
            raise RowError("must be a finite number")
        if t == "int":
            if float(raw) != int(float(raw)):
                raise RowError("must be a whole number")
            v = int(float(raw))
        else:
            v = float(raw)
        opts = col.options(d)
        if opts is not None and v not in opts:
            raise RowError(f"must be one of {opts}")
        return v
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        if _is_num(raw) and raw in (0, 1):
            return bool(raw)
        if isinstance(raw, str) or raw is None:
            s = (raw or "").strip().lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
        raise RowError("must be true / false")
    if t == "points":
        if blank:
            raise RowError("points are required")
        return parse_points(raw)
    # enum / text / ref
    if raw is None:
        v = ""
    elif isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise RowError("must be text")
    else:
        v = str(raw).strip() if not isinstance(raw, str) else raw.strip()
    if t == "enum":
        opts = col.options(d)
        if v not in opts:
            raise RowError(f"{v!r} is not one of {opts}")
    if t == "ref" and v:
        pool = _REF_POOLS[col.ref](d)
        if v not in pool:
            raise RowError(f"unknown {_REF_LABEL[col.ref]} {v!r}")
    return v


# --------------------------------------------------------------------------- #
# reference pools / dynamic enums
# --------------------------------------------------------------------------- #
def _names(key):
    f = lambda d: list((d.get(key) or {}).keys())          # noqa: E731
    f.refc = key
    return f


def _opt_names(key):
    f = lambda d: [""] + list((d.get(key) or {}).keys())   # noqa: E731
    f.refc = key
    return f


def _stories(d):
    return [s["name"] for s in d.get("stories") or []]


def _opt_stories(d):
    return [""] + _stories(d)


_opt_stories.refc = "stories"


def _stories_enum(d):
    return _stories(d)


_stories_enum.refc = "stories"


def _all_case_names(d) -> List[str]:
    out = []
    for k in ("cases", "rs_cases", "th_cases", "pushover_cases",
              "staged_cases", "buckling_cases", "steady_state_cases",
              "psd_cases", "nonlinear_static_cases", "hyperstatic_cases"):
        out += [n for n in (d.get(k) or {}) if n not in out]
    if "MODAL" not in out:
        out.append("MODAL")
    return out


def _combo_member_pool(d) -> List[str]:
    return _all_case_names(d) + [c for c in (d.get("combos") or {})
                                 if c not in _all_case_names(d)]


_combo_member_pool.refc = "*cases"
_CASE_CONTAINERS = ("cases", "rs_cases", "th_cases", "pushover_cases",
                    "staged_cases", "buckling_cases", "combos")

_REF_LABEL = {"members": "frame", "shells": "shell", "links": "link"}
_REF_POOLS: Dict[str, Callable[[dict], Any]] = {
    "members": lambda d: {m["uid"] for m in d.get("members") or []},
    "shells": lambda d: {r["uid"] for r in d.get("shells") or []},
    "links": lambda d: {lk["uid"] for lk in d.get("links") or []},
}


def _with_current(static, key):
    """Static enum plus any value currently stored (never reject legacy)."""
    def f(d):
        out = list(static)
        for v in _iter_values(d, key):
            if v not in out:
                out.append(v)
        return out
    return f


def _iter_values(d, key):
    cont, field = key
    src = d.get(cont) or {}
    for o in (src.values() if isinstance(src, dict) else src):
        v = o.get(field) if isinstance(o, dict) else None
        if isinstance(v, str):
            yield v


# --------------------------------------------------------------------------- #
# cascades (renames / deletions)
# --------------------------------------------------------------------------- #
def _rename_keys(dct, mapping):
    if not isinstance(dct, dict):
        return dct
    return {mapping.get(k, k): v for k, v in dct.items()}


def _cascade_material(d, mp):
    for s in (d.get("sections") or {}).values():
        s["material"] = mp.get(s.get("material"), s.get("material"))
    for s in (d.get("shell_sections") or {}).values():
        s["material"] = mp.get(s.get("material"), s.get("material"))
        for la in ((s.get("layered") or {}).get("layers") or []):
            la["material"] = mp.get(la.get("material"), la.get("material"))


def _cascade_section(d, mp):
    for m in d.get("members") or []:
        m["section"] = mp.get(m["section"], m["section"])
    for s in (d.get("sections") or {}).values():
        for seg in s.get("segments") or []:
            for k in ("start_section", "end_section"):
                if k in seg:
                    seg[k] = mp.get(seg[k], seg[k])


def _cascade_shell_section(d, mp):
    for r in d.get("shells") or []:
        r["section"] = mp.get(r.get("section"), r.get("section"))


def _cascade_pattern(d, mp):
    for c in (d.get("cases") or {}).values():
        c["patterns"] = _rename_keys(c.get("patterns"), mp)
        if c.get("pdelta_gravity"):
            c["pdelta_gravity"] = _rename_keys(c["pdelta_gravity"], mp)
    for k in ("mass_source", "mass_from_patterns"):
        if isinstance(d.get(k), dict):
            d[k] = _rename_keys(d[k], mp)
    for k in ("th_cases", "pushover_cases", "buckling_cases"):
        for c in (d.get(k) or {}).values():
            if isinstance(c.get("gravity"), dict):
                c["gravity"] = _rename_keys(c["gravity"], mp)
    for c in (d.get("staged_cases") or {}).values():
        c["pattern"] = mp.get(c.get("pattern"), c.get("pattern"))
        if isinstance(c.get("include_live"), dict):
            c["include_live"] = _rename_keys(c["include_live"], mp)


def _cascade_case(d, mp):
    for c in (d.get("combos") or {}).values():
        c["cases"] = _rename_keys(c.get("cases"), mp)
    d["cases_not_run"] = [mp.get(c, c) for c in d.get("cases_not_run") or []]
    for c in (d.get("rs_combos") or {}).values():
        for k in ("name_x", "name_y"):
            c[k] = mp.get(c.get(k), c.get(k))
    for c in (d.get("buckling_cases") or {}).values():
        if c.get("base_case"):
            c["base_case"] = mp.get(c["base_case"], c["base_case"])
    for c in (d.get("hyperstatic_cases") or {}).values():
        if isinstance(c, dict) and c.get("case"):
            c["case"] = mp.get(c["case"], c["case"])
    if d.get("modal_from_case"):
        d["modal_from_case"] = mp.get(d["modal_from_case"],
                                      d["modal_from_case"])


def _cascade_story(d, mp):
    for k in ("members", "shells"):
        for o in d.get(k) or []:
            o["story"] = mp.get(o.get("story"), o.get("story"))
    for k in ("story_diaphragm", "explicit_story_masses"):
        if isinstance(d.get(k), dict):
            d[k] = _rename_keys(d[k], mp)
    if "explicit_story_masses" in d:
        d.pop("story_masses", None)
    for p in (d.get("patterns") or {}).values():
        for s in p.get("story_forces") or []:
            s["story"] = mp.get(s.get("story"), s.get("story"))


def _uid_lists(d, kind):
    """(container list, key) pairs that reference a member / shell uid."""
    out = []
    for p in (d.get("patterns") or {}).values():
        if kind == "members":
            for k in ("member_udls", "member_loads", "thermal_loads"):
                out.append((p, k, "member_uid"))
        elif kind == "shells":
            out.append((p, "area_loads", "region_uid"))
            out.append((p, "shell_thermal_loads", "region_uid"))
    return out


def _cascade_uid(kind):
    def f(d, mp):
        for p, k, field in _uid_lists(d, kind):
            for e in p.get(k) or []:
                if isinstance(e, dict) and e.get(field) in mp:
                    e[field] = mp[e[field]]
        for g in (d.get("groups") or {}).values():
            if isinstance(g.get(kind), list):
                g[kind] = [mp.get(u, u) for u in g[kind]]
        if kind == "members":
            for c in (d.get("th_cases") or {}).values():
                if isinstance(c.get("My"), dict):
                    c["My"] = _rename_keys(c["My"], mp)
            for c in (d.get("pushover_cases") or {}).values():
                if isinstance(c.get("My"), dict):
                    c["My"] = _rename_keys(c["My"], mp)
    return f


def _delete_uid(kind):
    def f(d, gone):
        for p, k, field in _uid_lists(d, kind):
            if isinstance(p.get(k), list):
                p[k] = [e for e in p[k] if not (isinstance(e, dict)
                                                and e.get(field) in gone)]
        for g in (d.get("groups") or {}).values():
            if isinstance(g.get(kind), list):
                g[kind] = [u for u in g[kind] if u not in gone]
    return f


def _cascade_group(d, mp):
    for c in d.get("section_cuts") or []:
        if c.get("group") in mp:
            c["group"] = mp[c["group"]]


def _cascade_diaphragm(d, mp):
    for r in d.get("shells") or []:
        if r.get("diaphragm") in mp:
            r["diaphragm"] = mp[r["diaphragm"]]
    for e in d.get("joint_diaphragms") or []:
        if e.get("diaphragm") in mp:
            e["diaphragm"] = mp[e["diaphragm"]]


def _delete_story(d, gone):
    if isinstance(d.get("story_diaphragm"), dict):
        d["story_diaphragm"] = {k: v for k, v in d["story_diaphragm"].items()
                                if k not in gone}


# --------------------------------------------------------------------------- #
# table base classes
# --------------------------------------------------------------------------- #
class Table:
    key = ""
    container = ""
    title = ""
    group = ""
    editable = True
    can_add = True
    can_delete = True
    keycol: Optional[str] = None

    def __init__(self, key, title, group, cols, **kw):
        self.key, self.title, self.group, self.cols = key, title, group, cols
        for k, v in kw.items():
            setattr(self, k, v)

    def col(self, key) -> Col:
        for c in self.cols:
            if c.key == key:
                return c
        raise KeyError(key)

    def meta(self, d) -> dict:
        return {"key": self.key, "title": self.title, "group": self.group,
                "editable": self.editable, "can_add": self.can_add,
                "can_delete": self.can_delete, "key_column": self.keycol}

    def rows(self, d) -> List[dict]:
        raise NotImplementedError

    def apply(self, d, rows, errs) -> dict:
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def _row_values(self, i, row, base, d, errs, is_new, skip=()):
        """Parse the editable cells of ``row`` into ``base`` (in place).
        Cells equal to the stored value are not written (and not
        re-validated, so legacy values survive an unrelated edit)."""
        for c in self.cols:
            if not c.editable or c.key in skip:
                continue
            if not is_new and c.key in row:
                cur0 = c.get(base, d)
                raw0 = row[c.key]
                if type(raw0) is type(cur0) and _same(raw0, cur0):
                    continue
            if c.key not in row:
                if is_new and c.required:
                    errs.append(_err(self.key, i, c.key,
                                     f"{c.label} is required"))
                continue
            raw = row[c.key]
            try:
                v = parse_cell(c, raw, d)
            except RowError as exc:
                errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
                continue
            if is_new and c.required and v in ("", None):
                errs.append(_err(self.key, i, c.key, f"{c.label} is required"))
                continue
            cur = c.get(base, d)
            if not is_new and _same(cur, v):
                continue
            try:
                c.put(base, v, d)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))


def _same(a, b) -> bool:
    if _is_num(a) and _is_num(b):
        return float(a) == float(b)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def _err(table, row, col, msg) -> dict:
    return {"table": table, "row": row, "col": col, "message": msg}


def _rid(row):
    rid = row.get("_id")
    if isinstance(rid, str) and rid.startswith("["):
        try:
            rid = json.loads(rid)
        except ValueError:
            pass
    return rid


class ObjectTable(Table):
    """Rows = the objects of a top-level dict (keyed by name) or list."""

    container = ""
    kind = "dict"                 # "dict" | "list"
    name_in_obj = True            # dict objects carry their own "name"
    auto_prefix = ""              # list tables: auto uid for blank keys
    on_rename: Optional[Callable] = None
    on_delete: Optional[Callable] = None
    new_obj: Callable = staticmethod(lambda d: {})
    post: Optional[Callable] = None          # post(d, pairs, orig_by_id)
    delete_refs: Optional[Callable] = None   # f(d, gone) -> error msg | None
    validate_new: Optional[Callable] = None  # f(obj) -> (col, msg) | None

    def _items(self, d):
        src = d.get(self.container)
        if self.kind == "dict":
            return list((src or {}).items())
        return list(enumerate(src or []))

    def rows(self, d):
        out = []
        for rid, obj in self._items(d):
            r = {"_id": rid}
            for c in self.cols:
                if self.kind == "dict" and c.key == self.keycol:
                    r[c.key] = rid
                else:
                    r[c.key] = c.get(obj, d)
            out.append(r)
        return out

    def apply(self, d, rows, errs):
        items = self._items(d)
        orig = {rid: obj for rid, obj in items}
        key_of = {}
        for rid, obj in items:
            if self.kind == "dict":
                key_of[rid] = rid
            elif self.keycol:
                key_of[rid] = obj.get(self.keycol)
        by_key = {k: rid for rid, k in key_of.items()}
        seen, pairs = set(), []
        n0 = len(errs)
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            rid = _rid(row)
            if rid is not None and rid not in orig:
                rid = None          # stale id: treat as new / key match
            if rid is None and self.keycol:
                kv = row.get(self.keycol)
                kv = kv.strip() if isinstance(kv, str) else kv
                if kv in by_key and by_key[kv] not in seen:
                    rid = by_key[kv]
            if rid is not None and rid in seen:
                errs.append(_err(self.key, i, None,
                                 "row duplicates another row's object"))
                continue
            if not self.can_add and rid is None:
                errs.append(_err(self.key, i, None,
                                 "rows cannot be added to this table"))
                continue
            is_new = rid is None
            base = copy.deepcopy(orig[rid]) if not is_new else self.new_obj(d)
            self._row_values(i, row, base, d, errs, is_new,
                             skip=(self.keycol,) if self.kind == "dict"
                             else ())
            if is_new and self.validate_new:
                bad = self.validate_new(base)
                if bad:
                    errs.append(_err(self.key, i, bad[0], bad[1]))
            if rid is not None:
                seen.add(rid)
            pairs.append((i, rid, base))
        if not self.can_delete:
            missing = [rid for rid in orig if rid not in seen]
            if missing:
                errs.append(_err(self.key, None, None,
                                 f"rows cannot be deleted from this table "
                                 f"(missing {len(missing)})"))
        if len(errs) > n0:
            return {}
        # keys: required, unique, auto-generated for blank uids
        keys, renames = [], {}
        if self.keycol:
            used = set()
            taken = {o.get(self.keycol) for _, _, o in pairs}
            for i, rid, obj in pairs:
                if self.kind == "dict":
                    k = rows[i].get(self.keycol, rid if rid is not None else "")
                else:
                    k = obj.get(self.keycol)
                k = str(k).strip() if k is not None else ""
                if not k and self.auto_prefix:
                    n = 1
                    while f"{self.auto_prefix}{n}" in taken | used:
                        n += 1
                    k = f"{self.auto_prefix}{n}"
                if self.kind == "list" and obj.get(self.keycol) != k:
                    obj[self.keycol] = k
                if not k:
                    errs.append(_err(self.key, i, self.keycol,
                                     f"{self.col(self.keycol).label} "
                                     "is required"))
                    continue
                if k in used:
                    errs.append(_err(self.key, i, self.keycol,
                                     f"duplicate {self.col(self.keycol).label}"
                                     f" {k!r}"))
                    continue
                used.add(k)
                keys.append(k)
                if rid is not None and key_of.get(rid) != k:
                    renames[key_of[rid]] = k
            if len(errs) > n0:
                return {}
        gone = [key_of.get(rid, rid) for rid in orig if rid not in seen]
        if gone and self.delete_refs:
            msg = self.delete_refs(d, set(gone))
            if msg:
                errs.append(_err(self.key, None, None, msg))
                return {}
        # write back
        if self.kind == "dict":
            new = {}
            for (i, rid, obj), k in zip(pairs, keys):
                if self.name_in_obj:
                    obj["name"] = k
                new[k] = obj
            d[self.container] = new
        else:
            d[self.container] = [obj for _, _, obj in pairs]
        if self.post:
            self.post(d, pairs, orig)
        if renames and self.on_rename:
            self.on_rename(d, renames)
        if gone and self.on_delete:
            self.on_delete(d, set(gone))
        added = sum(1 for _, rid, _ in pairs if rid is None)
        modified = sum(1 for _, rid, obj in pairs
                       if rid is not None and obj != orig[rid])
        out = {"added": added, "modified": modified, "deleted": len(gone)}
        if renames:
            out["renamed"] = renames
        return out


class PatternListTable(Table):
    """Rows = entries of per-pattern load lists (joint / frame / area)."""

    lists: Tuple[str, ...] = ()
    belongs: Callable = staticmethod(lambda lst, e: True)
    to_row_obj: Callable = staticmethod(lambda lst, e: copy.deepcopy(e))
    target: Callable = staticmethod(lambda lst, obj: (lst, obj))
    new_obj: Callable = staticmethod(lambda d: {})
    default_list = ""

    def _items(self, d):
        for pname, p in (d.get("patterns") or {}).items():
            for lst in self.lists:
                for k, e in enumerate(p.get(lst) or []):
                    if self.belongs(lst, e):
                        yield [pname, lst, k], e

    def rows(self, d):
        out = []
        for rid, e in self._items(d):
            obj = self.to_row_obj(rid[1], e)
            r = {"_id": rid, "pattern": rid[0]}
            for c in self.cols:
                if c.key != "pattern":
                    r[c.key] = c.get(obj, d)
            out.append(r)
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        orig = {tuple(rid): e for rid, e in self._items(d)}
        seen, placed = set(), []
        pats = d.get("patterns") or {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            rid = _rid(row)
            rid = tuple(rid) if isinstance(rid, list) else None
            if rid is not None and rid not in orig:
                rid = None
            if rid is not None and rid in seen:
                errs.append(_err(self.key, i, None,
                                 "row duplicates another row's load"))
                continue
            is_new = rid is None
            pname = row.get("pattern", rid[0] if rid else None)
            pname = pname.strip() if isinstance(pname, str) else pname
            if not pname or pname not in pats:
                errs.append(_err(self.key, i, "pattern",
                                 f"unknown load pattern {pname!r}"))
                continue
            base = (self.to_row_obj(rid[1], copy.deepcopy(orig[rid]))
                    if not is_new else self.new_obj(d))
            self._row_values(i, row, base, d, errs, is_new, skip=("pattern",))
            if rid is not None:
                seen.add(rid)
            placed.append((rid, pname, base))
        if len(errs) > n0:
            return {}
        slot = {}
        extra = []
        for rid, pname, obj in placed:
            lst, out = self.target(rid[1] if rid else None, obj)
            if rid is not None and rid[0] == pname and rid[1] == lst:
                slot[rid] = out
            else:
                extra.append((pname, lst, out))
        deleted = sum(1 for rid in orig if rid not in seen)
        for pname, p in pats.items():
            for lst in self.lists:
                old = p.get(lst) or []
                new = []
                for k, e in enumerate(old):
                    rid = (pname, lst, k)
                    if not self.belongs(lst, e):
                        new.append(e)
                    elif rid in slot:
                        new.append(slot[rid])
                new += [o for pn, ls, o in extra if pn == pname and ls == lst]
                if new or lst in p:
                    p[lst] = new
        modified = sum(1 for rid, o in slot.items() if o != orig[rid])
        return {"added": sum(1 for r, _, _ in placed if r is None),
                "modified": modified, "deleted": deleted}


class ChildDictTable(Table):
    """Rows = (parent, child, value) of a dict nested in each parent object
    (combo members, static-case pattern factors) or of a top-level dict
    (``parent_container is None``: mass source)."""

    container = ""
    field = ""
    parent_col = ""
    child_col = ""
    value_col = ""

    def _parents(self, d):
        if self.container is None:
            return None
        return d.get(self.container) or {}

    def rows(self, d):
        out = []
        parents = self._parents(d)
        if parents is None:
            for k, v in (d.get(self.field) or {}).items():
                out.append({"_id": [k], self.child_col: k, self.value_col: v})
            return out
        for pn, p in parents.items():
            for k, v in (p.get(self.field) or {}).items():
                out.append({"_id": [pn, k], self.parent_col: pn,
                            self.child_col: k, self.value_col: v})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        parents = self._parents(d)
        vcol, ccol = self.col(self.value_col), self.col(self.child_col)
        built: Dict[Any, Dict[str, Any]] = {}
        seen = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            if parents is not None:
                pn = row.get(self.parent_col)
                pn = pn.strip() if isinstance(pn, str) else pn
                if pn not in parents:
                    errs.append(_err(self.key, i, self.parent_col,
                                     f"unknown {self.col(self.parent_col).label}"
                                     f" {pn!r}"))
                    continue
            else:
                pn = None
            try:
                child = parse_cell(ccol, row.get(self.child_col), d)
                if not child:
                    raise RowError("is required")
            except RowError as exc:
                errs.append(_err(self.key, i, self.child_col,
                                 f"{ccol.label}: {exc}"))
                continue
            try:
                val = parse_cell(vcol, row.get(self.value_col), d)
            except RowError as exc:
                errs.append(_err(self.key, i, self.value_col,
                                 f"{vcol.label}: {exc}"))
                continue
            if (pn, child) in seen:
                errs.append(_err(self.key, i, self.child_col,
                                 f"duplicate {ccol.label} {child!r}"))
                continue
            seen.add((pn, child))
            built.setdefault(pn, {})[child] = val
        if len(errs) > n0:
            return {}
        if parents is None:
            old = d.get(self.field) or {}
            d[self.field] = _merge_ordered(old, built.get(None, {}))
            return {"rows": len(rows), "changed": old != d[self.field]}
        changed = 0
        for pn, p in parents.items():
            old = p.get(self.field) or {}
            p[self.field] = _merge_ordered(old, built.get(pn, {}))
            changed += old != p[self.field]
        return {"rows": len(rows), "parents_changed": changed}


def _merge_ordered(old: dict, new: dict) -> dict:
    """``new``'s entries, keeping ``old``'s key order for surviving keys."""
    out = {k: _keep_num(old[k], new[k]) for k in old if k in new}
    for k, v in new.items():
        if k not in out:
            out[k] = v
    return out


def _keep_num(a, b):
    return a if _same(a, b) else b


# --------------------------------------------------------------------------- #
# per-table helpers
# --------------------------------------------------------------------------- #
def _xyz(prefix, key, label, required=True):
    return [Col(f"{prefix}x", f"{label}X", quantity="length", path=(key, 0),
                required=required),
            Col(f"{prefix}y", f"{label}Y", quantity="length", path=(key, 1),
                required=required),
            Col(f"{prefix}z", f"{label}Z", quantity="length", path=(key, 2),
                required=required)]


def _put_point(key, idx):
    def put(obj, v, d):
        p = list(obj.get(key) or [0.0, 0.0, 0.0])
        while len(p) < 3:
            p.append(0.0)
        p[idx] = v
        obj[key] = p
    return put


def _pt_cols(prefix, key, label, required=True):
    cols = _xyz(prefix, key, label, required)
    for i, c in enumerate(cols):
        c._put = _put_point(key, i)
    return cols


def _k6_cols(key="stiffness", rot_q="rot_stiffness"):
    labels = ("kx", "ky", "kz", "krx", "kry", "krz")
    out = []
    for i, lb in enumerate(labels):
        def put(obj, v, d, i=i):
            k = list(obj.get(key) or [0.0] * 6)
            k[i] = v
            obj[key] = k
        out.append(Col(lb, lb.upper().replace("KR", "KR"), quantity=(
            "stiffness" if i < 3 else rot_q), path=(key, i), default=0.0,
            put=put))
    return out


def _release_col(tok, label):
    def get(obj, d):
        return tok in {t.strip() for t in str(obj.get("releases", "")
                                               ).split(",") if t.strip()}

    def put(obj, v, d):
        toks = [t.strip() for t in str(obj.get("releases", "")).split(",")
                if t.strip()]
        if v and tok not in toks:
            toks.append(tok)
        if not v:
            toks = [t for t in toks if t != tok]
        order = {"Mi": 0, "Mj": 1}
        toks.sort(key=lambda t: order.get(t, 9))
        obj["releases"] = ",".join(toks)
    return Col(f"release_{tok[-1]}", f"Release {label}", "bool", get=get,
               put=put, default=False)


def _section_post(d, pairs, orig):
    """Rectangle sections: an edited b / h with unchanged A / I recomputes
    A, I33, I22, J exactly as ``FrameSection.rectangular``."""
    from skyframe.core.model import FrameSection
    for _i, rid, s in pairs:
        for k in ("A", "I33", "I22", "J"):
            if k in s and s[k] is None:
                del s[k]
        if rid is None or (s.get("kind") or "prismatic") != "prismatic":
            continue
        o = orig[rid]
        if rid in (d.get("designer_sections") or {}):
            continue
        dims_changed = (s.get("b") != o.get("b") or s.get("h") != o.get("h"))
        props_same = all(s.get(k) == o.get(k) for k in ("A", "I33", "I22", "J"))
        b, h = float(s.get("b") or 0), float(s.get("h") or 0)
        ob, oh = float(o.get("b") or 0), float(o.get("h") or 0)
        was_rect = ob > 0 and oh > 0 and math.isclose(
            float(o.get("A") or 0), ob * oh, rel_tol=1e-9)
        if dims_changed and props_same and was_rect and b > 0 and h > 0:
            r = FrameSection.rectangular("x", "x", b, h)
            s.update({"A": r.A, "I33": r.I33, "I22": r.I22, "J": r.J})


def _section_new_ok(s):
    props = [s.get(k) for k in ("A", "I33", "I22", "J")]
    if all(p is not None for p in props):
        return None
    if all(p is None for p in props) and float(s.get("b") or 0) > 0 \
            and float(s.get("h") or 0) > 0:
        return None
    return ("A", "give all of A / I33 / I22 / J, or b and h (> 0) for a "
                 "rectangle")


def _new_section(d):
    return {"name": "", "material": "", "b": 0.0, "h": 0.0}


def _story_post(d, pairs, orig):
    """Recompute elevations (cumulative heights from the original base)
    when any height / order changed; otherwise keep the stored values."""
    stories = d.get("stories") or []
    old = [orig[k] for k in sorted(orig)]
    same = (len(old) == len(stories) and all(
        _same(a.get("height"), b.get("height")) for a, b in
        zip(old, stories)) and all(rid == k for k, (_i, rid, _o) in
                                   enumerate(pairs)))
    if same:
        for s, o in zip(stories, old):
            s["elevation"] = o.get("elevation", 0.0)
        return
    base = (float(old[0].get("elevation", 0.0)) - float(old[0]["height"])
            if old else 0.0)
    if abs(base) < 1e-12:
        base = 0.0
    z = base
    for s in stories:
        z += float(s["height"])
        s["elevation"] = z


def _material_refs(d, gone):
    for s in (d.get("sections") or {}).values():
        if s.get("material") in gone:
            return (f"material {s['material']!r} is used by frame section "
                    f"{s.get('name')!r}")
    for s in (d.get("shell_sections") or {}).values():
        if s.get("material") in gone:
            return (f"material {s['material']!r} is used by shell section "
                    f"{s.get('name')!r}")
    return None


def _section_refs(d, gone):
    for m in d.get("members") or []:
        if m.get("section") in gone:
            return (f"frame section {m['section']!r} is used by frame "
                    f"{m['uid']!r}")
    return None


def _shell_section_refs(d, gone):
    for r in d.get("shells") or []:
        if r.get("section") in gone:
            return (f"shell section {r['section']!r} is used by shell "
                    f"{r['uid']!r}")
    return None


def _pattern_refs(d, gone):
    for c in (d.get("cases") or {}).values():
        for p in c.get("patterns") or {}:
            if p in gone:
                return f"load pattern {p!r} is used by load case {c['name']!r}"
    for p in (d.get("mass_source") or {}):
        if p in gone:
            return f"load pattern {p!r} is used by the mass source"
    return None


def _case_refs(d, gone):
    for c in (d.get("combos") or {}).values():
        for m in c.get("cases") or {}:
            if m in gone:
                return (f"{m!r} is used by load combination "
                        f"{c.get('name')!r}")
    return None


# --------------------------------------------------------------------------- #
# derived / special tables
# --------------------------------------------------------------------------- #
def _pkey(p) -> Tuple[float, float, float]:
    return tuple(round(float(v), 6) + 0.0 for v in p)


class JointsTable(Table):
    editable = False
    can_add = False
    can_delete = False

    def rows(self, d):
        joints: Dict[tuple, dict] = {}

        def add(p, what):
            k = _pkey(p)
            j = joints.get(k)
            if j is None:
                j = joints[k] = {"x": float(p[0]), "y": float(p[1]),
                                 "z": float(p[2]), "frames": 0, "shells": 0,
                                 "links": 0, "support": ""}
            if what:
                j[what] += 1
            return j
        for m in d.get("members") or []:
            add(m["pi"], "frames")
            add(m["pj"], "frames")
        for r in d.get("shells") or []:
            for c in r.get("corners") or []:
                add(c, "shells")
        for lk in d.get("links") or []:
            add(lk["pi"], "links")
            add(lk["pj"], "links")
        for s in d.get("supports") or []:
            r = s.get("restraints") or []
            add(s["point"], None)["support"] = (
                "fixed" if all(r) else "pinned" if list(r) == [1, 1, 1, 0, 0, 0]
                else "".join("1" if v else "0" for v in r))
        for s in d.get("spring_supports") or []:
            j = add(s["point"], None)
            j["support"] = (j["support"] + " spring").strip()
        elev = {round(float(s.get("elevation", 0.0)), 6): s["name"]
                for s in d.get("stories") or []}
        zs = [round(float(s.get("elevation", 0.0)) - float(s["height"]), 6)
              for s in (d.get("stories") or [])[:1]]
        out = []
        for n, (k, j) in enumerate(sorted(joints.items(),
                                          key=lambda kv: (kv[0][2], kv[0][1],
                                                          kv[0][0]))):
            story = elev.get(round(j["z"], 6), "Base" if zs and
                             abs(j["z"] - zs[0]) < 1e-6 else "")
            out.append({"_id": n, "id": f"J{n + 1}", "story": story, **j})
        return out

    def apply(self, d, rows, errs):
        errs.append(_err(self.key, None, None,
                         "the joint table is derived (read only): edit the "
                         "object coordinates instead"))
        return {}


class CaseSummaryTable(Table):
    editable = False
    can_add = False
    can_delete = False

    def rows(self, d):
        kinds = (("cases", "Linear Static"), ("rs_cases", "Response Spectrum"),
                 ("th_cases", "Time History"), ("pushover_cases", "Pushover"),
                 ("staged_cases", "Staged Construction"),
                 ("buckling_cases", "Buckling"),
                 ("steady_state_cases", "Steady State"), ("psd_cases", "PSD"),
                 ("nonlinear_static_cases", "Nonlinear Static"),
                 ("hyperstatic_cases", "Hyperstatic"))
        out, seen = [], set()
        not_run = set(d.get("cases_not_run") or [])
        for cont, label in kinds:
            for n in (d.get(cont) or {}):
                if n in seen:
                    continue
                seen.add(n)
                out.append({"_id": n, "name": n, "type": label,
                            "run": n not in not_run})
        if "MODAL" not in seen:
            out.append({"_id": "MODAL", "name": "MODAL", "type": "Modal",
                        "run": "MODAL" not in not_run})
        return out

    def apply(self, d, rows, errs):
        errs.append(_err(self.key, None, None,
                         "the load-case summary is read only: edit the "
                         "per-type case tables"))
        return {}


class GridLinesTable(Table):
    keycol = None

    def rows(self, d):
        out = []
        for g in _grid_list(d):
            kind = g.get("kind", "orthogonal")
            axes = ((("x", "x_lines", "x_labels"), ("y", "y_lines", "y_labels"))
                    if kind == "orthogonal" else
                    (("radius", "radii", None), ("theta", "theta_deg", None)))
            for ax, key, lab in axes:
                labels = g.get(lab) or [] if lab else []
                for k, v in enumerate(g.get(key) or []):
                    out.append({"_id": [g["name"], ax, k], "system": g["name"],
                                "axis": ax,
                                "label": labels[k] if k < len(labels) else
                                f"{'R' if ax == 'radius' else 'T'}{k + 1}",
                                "ordinate": v})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        grids = _grid_list(d)
        by = {g["name"]: g for g in grids}
        acc: Dict[str, Dict[str, list]] = {}
        new_names = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            sysn = row.get("system")
            sysn = sysn.strip() if isinstance(sysn, str) else ""
            if not sysn:
                errs.append(_err(self.key, i, "system",
                                 "Grid System is required"))
                continue
            try:
                ax = parse_cell(self.col("axis"), row.get("axis"), d)
                v = parse_cell(self.col("ordinate"), row.get("ordinate"), d)
            except RowError as exc:
                col = "axis" if "axis" not in row or not isinstance(
                    row.get("axis"), str) or row.get("axis") not in (
                    "x", "y", "radius", "theta") else "ordinate"
                errs.append(_err(self.key, i, col, str(exc)))
                continue
            kind = (by[sysn].get("kind", "orthogonal") if sysn in by
                    else "orthogonal")
            if (kind == "orthogonal") != (ax in ("x", "y")):
                errs.append(_err(self.key, i, "axis",
                                 f"axis {ax!r} does not fit the {kind} grid "
                                 f"{sysn!r}"))
                continue
            if sysn not in by and sysn not in new_names:
                new_names.append(sysn)
            acc.setdefault(sysn, {}).setdefault(ax, []).append(v)
        if len(errs) > n0:
            return {}
        key_of = {"x": "x_lines", "y": "y_lines", "radius": "radii",
                  "theta": "theta_deg"}
        for g in grids:
            got = acc.get(g["name"], {})
            axes = ("x", "y") if g.get("kind", "orthogonal") == "orthogonal" \
                else ("radius", "theta")
            for ax in axes:
                old = g.get(key_of[ax]) or []
                new = got.get(ax, [])
                if not _same(old, new):
                    g[key_of[ax]] = new
        for n in new_names:
            got = acc[n]
            grids.append({"name": n, "kind": "orthogonal", "origin": [0.0, 0.0],
                          "rotation": 0.0, "x_lines": got.get("x", []),
                          "y_lines": got.get("y", []), "radii": [],
                          "theta_deg": []})
        d["grid_systems"] = grids
        d["grid"] = grids[0] if grids else None
        return {"rows": len(rows), "systems_added": len(new_names)}


def _grid_list(d) -> List[dict]:
    gs = d.get("grid_systems")
    if gs:
        return gs
    return [d["grid"]] if d.get("grid") else []


class StoryDiaphragmTable(Table):
    keycol = "story"
    can_add = False
    can_delete = False

    def rows(self, d):
        sd = d.get("story_diaphragm") or {}
        return [{"_id": s["name"], "story": s["name"],
                 "diaphragm": sd.get(s["name"], "default")}
                for s in d.get("stories") or []]

    def apply(self, d, rows, errs):
        n0 = len(errs)
        names = set(_stories(d))
        built = {}
        for i, row in enumerate(rows):
            st = (row.get("story", row.get("_id"))
                  if isinstance(row, dict) else None)
            if st not in names:
                errs.append(_err(self.key, i, "story", f"unknown story {st!r}"))
                continue
            try:
                v = parse_cell(self.col("diaphragm"), row.get("diaphragm"), d)
            except RowError as exc:
                errs.append(_err(self.key, i, "diaphragm", str(exc)))
                continue
            if st in built:
                errs.append(_err(self.key, i, "story", f"duplicate story {st!r}"))
                continue
            built[st] = v
        if len(errs) > n0:
            return {}
        old = d.get("story_diaphragm") or {}
        new = {k: v for k, v in built.items() if v != "default"}
        new = {**{k: new[k] for k in old if k in new},
               **{k: v for k, v in new.items() if k not in old}}
        for k, v in old.items():           # stories not listed keep theirs
            if k not in built:
                new[k] = v
        d["story_diaphragm"] = new
        return {"rows": len(rows)}


class MassOptionsTable(Table):
    can_add = False
    can_delete = False
    OPTS = ("self_mass", "patterns", "include_lateral", "include_vertical",
            "lump_at_stories")

    def rows(self, d):
        mo = d.get("mass_options") or {}
        from skyframe.core.model import MASS_OPTION_DEFAULTS
        r = {"_id": 0, "mode": d.get("mass_source_mode", "weight")}
        for k in self.OPTS:
            r[k] = bool(mo.get(k, MASS_OPTION_DEFAULTS[k]))
        return [r]

    def apply(self, d, rows, errs):
        if len(rows) != 1 or not isinstance(rows[0], dict):
            errs.append(_err(self.key, None, None,
                             "the mass source options table has exactly "
                             "one row"))
            return {}
        n0 = len(errs)
        row = rows[0]
        vals = {}
        for c in self.cols:
            if c.key not in row:
                continue
            try:
                vals[c.key] = parse_cell(c, row[c.key], d)
            except RowError as exc:
                errs.append(_err(self.key, 0, c.key, f"{c.label}: {exc}"))
        if len(errs) > n0:
            return {}
        if "mode" in vals:
            d["mass_source_mode"] = vals.pop("mode")
        mo = dict(d.get("mass_options") or {})
        for k, v in vals.items():
            mo[k] = v
        d["mass_options"] = mo
        return {"rows": 1}


class GroupAssignTable(Table):
    KINDS = ("members", "shells", "links")
    LABEL = {"members": "frame", "shells": "shell", "links": "link"}

    def rows(self, d):
        out = []
        for gn, g in (d.get("groups") or {}).items():
            for kind in self.KINDS:
                for u in g.get(kind) or []:
                    out.append({"_id": [gn, kind, u], "group": gn,
                                "type": self.LABEL[kind], "object": u})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        groups = d.get("groups") or {}
        inv = {v: k for k, v in self.LABEL.items()}
        built: Dict[str, Dict[str, list]] = {}
        seen = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            gn = row.get("group")
            if gn not in groups:
                errs.append(_err(self.key, i, "group", f"unknown group {gn!r}"))
                continue
            t = row.get("type")
            if t not in inv:
                errs.append(_err(self.key, i, "type",
                                 f"{t!r} is not one of {list(inv)}"))
                continue
            u = row.get("object")
            u = str(u).strip() if u is not None else ""
            if u not in _REF_POOLS[inv[t]](d):
                errs.append(_err(self.key, i, "object",
                                 f"unknown {t} {u!r}"))
                continue
            if (gn, t, u) in seen:
                errs.append(_err(self.key, i, "object",
                                 f"{t} {u!r} is listed twice in {gn!r}"))
                continue
            seen.add((gn, t, u))
            built.setdefault(gn, {}).setdefault(inv[t], []).append(u)
        if len(errs) > n0:
            return {}
        for gn, g in groups.items():
            for kind in self.KINDS:
                new = built.get(gn, {}).get(kind, [])
                if (g.get(kind) or []) != new:
                    g[kind] = new
        return {"rows": len(rows)}


# --------------------------------------------------------------------------- #
# member-load representation (legacy member_udls + member_loads)
# --------------------------------------------------------------------------- #
def _udl_as_load(lst, e):
    if lst == "member_udls":
        return {"member_uid": e["member_uid"], "kind": "udl", "w": e["w"],
                "w2": 0.0, "a": 0.0, "b": 1.0, "direction": "gravity"}
    return copy.deepcopy(e)


def _dist_target(lst, obj):
    if (lst in ("member_udls", None) and obj.get("kind") == "udl"
            and obj.get("direction") == "gravity"
            and float(obj.get("a", 0.0)) == 0.0
            and float(obj.get("b", 1.0)) == 1.0
            and not obj.get("projected") and float(obj.get("w2", 0.0)) == 0.0):
        return "member_udls", {"member_uid": obj["member_uid"],
                               "w": obj["w"]}
    out = dict(obj)
    if not out.get("projected"):
        out.pop("projected", None)
    return "member_loads", out


def _ml_target(lst, obj):
    out = dict(obj)
    if not out.get("projected"):
        out.pop("projected", None)
    return "member_loads", out


def _nodal_target(lst, obj):
    out = {"point": obj["point"], "fx": obj.get("fx", 0.0),
           "fy": obj.get("fy", 0.0), "fz": obj.get("fz", 0.0)}
    for k in ("mx", "my", "mz"):
        if obj.get(k):
            out[k] = obj[k]
    return "nodal_loads", out


def _area_target(lst, obj):
    out = {"region_uid": obj["region_uid"], "q": obj["q"]}
    if obj.get("direction", "gravity") != "gravity":
        out["direction"] = obj["direction"]
    if obj.get("projected"):
        out["projected"] = True
    if obj.get("joint_pattern") is not None:
        out["joint_pattern"] = obj["joint_pattern"]
    return "area_loads", out


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
def _build_catalogue() -> List[Table]:
    from skyframe.core.model import (
        MATERIAL_TYPES, MATERIAL_SYMMETRY, AXIAL_LIMITS, GEOMETRIC_OPTIONS,
        RS_DIRECTIONS, RS_COMBO_METHODS, TH_DIRECTIONS, TH_DAMPING_MODELS,
        PUSHOVER_DIRECTIONS, PUSHOVER_HINGE_MODES, COMBO_TYPES, LINK_TYPES,
        MEMBER_LOAD_DIRECTIONS, MASS_SOURCE_MODES, DIAPHRAGM_OPTIONS)
    from skyframe.core.loads_ext import AREA_LOAD_DIRECTIONS
    from skyframe.core.insertion import END_OFFSET_MODES
    from skyframe.core.diaphragms import DIAPHRAGM_TYPES

    T: List[Table] = []
    G_DEF, G_OBJ, G_ASN, G_LOAD, G_CASE = (
        "Model Definition > Properties", "Model Definition > Objects",
        "Model Definition > Assignments", "Model Definition > Loads",
        "Model Definition > Load Cases & Combinations")
    num = "number"

    # ---- materials
    T.append(ObjectTable(
        "materials", "Material Properties", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("material_type", "Type", "enum",
                enum=_with_current(MATERIAL_TYPES,
                                   ("materials", "material_type")),
                default="concrete"),
            Col("symmetry", "Symmetry", "enum", enum=MATERIAL_SYMMETRY,
                default="isotropic"),
            Col("E", "E", num, "modulus", required=True),
            Col("nu", "ν", num, default=0.2),
            Col("G", "G", num, "modulus", editable=False),
            Col("unit_weight", "Unit Weight", num, "unit_weight",
                default=24.0),
            Col("mass_density", "Mass Density", num, "mass_density",
                optional=True),
            Col("alpha", "α (thermal)", num, "thermal_coeff", optional=True),
            Col("fc", "f'c", num, "stress", optional=True),
            Col("fy", "Fy", num, "stress", optional=True),
            Col("fu", "Fu", num, "stress", optional=True),
            Col("Ry", "Ry", num, default=1.1),
            Col("damping", "Damping", num, default=0.0),
            Col("notes", "Notes", "text", default=""),
        ], container="materials", keycol="name",
        on_rename=_cascade_material, delete_refs=_material_refs,
        new_obj=lambda d: {"nu": 0.2, "unit_weight": 24.0}))

    # ---- frame sections
    T.append(ObjectTable(
        "frame_sections", "Frame Section Properties", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("material", "Material", "enum", enum=_names("materials"),
                required=True),
            Col("kind", "Kind", "text", default="prismatic", editable=False),
            Col("b", "b (width)", num, "dim", default=0.0),
            Col("h", "h (depth)", num, "dim", default=0.0),
            Col("A", "A", num, "area", optional=True),
            Col("I33", "I33", num, "inertia", optional=True),
            Col("I22", "I22", num, "inertia", optional=True),
            Col("J", "J", num, "inertia", optional=True),
            Col("As2", "As2", num, "area", optional=True),
            Col("As3", "As3", num, "area", optional=True),
            Col("mod_A", "A mod", num, default=1.0),
            Col("mod_I33", "I33 mod", num, default=1.0),
            Col("mod_I22", "I22 mod", num, default=1.0),
            Col("mod_J", "J mod", num, default=1.0),
            Col("mod_mass", "Mass mod", num, default=1.0),
            Col("mod_weight", "Weight mod", num, default=1.0),
        ], container="sections", keycol="name",
        on_rename=_cascade_section, delete_refs=_section_refs,
        new_obj=_new_section, post=_section_post,
        validate_new=_section_new_ok))

    # ---- shell sections
    T.append(ObjectTable(
        "shell_sections", "Shell Section Properties", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("material", "Material", "enum", enum=_names("materials"),
                required=True),
            Col("thickness", "Thickness", num, "dim", required=True),
            Col("mod", "Stiffness mod", num, default=1.0),
            *[Col(k, f"{k} mod", num, default=1.0) for k in
              ("f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23")],
            Col("mass", "Mass mod", num, default=1.0),
            Col("weight", "Weight mod", num, default=1.0),
        ], container="shell_sections", keycol="name",
        on_rename=_cascade_shell_section, delete_refs=_shell_section_refs,
        new_obj=lambda d: {"mod": 1.0, "layered": None}))

    # ---- stories / grids / joints
    T.append(ObjectTable(
        "stories", "Story Definitions", G_DEF, [
            Col("name", "Story", "text", required=True),
            Col("height", "Height", num, "length", required=True),
            Col("elevation", "Elevation", num, "length", editable=False),
        ], container="stories", kind="list", keycol="name",
        on_rename=_cascade_story, on_delete=_delete_story, post=_story_post,
        new_obj=lambda d: {"name": "", "height": 3.0, "elevation": 0.0}))
    T.append(GridLinesTable(
        "grid_lines", "Grid Lines", G_DEF, [
            Col("system", "Grid System", "text", required=True),
            Col("axis", "Axis", "enum", enum=("x", "y", "radius", "theta"),
                required=True),
            Col("label", "Label", "text", editable=False),
            Col("ordinate", "Ordinate", num, "length", required=True),
        ]))
    T.append(JointsTable(
        "joints", "Joint Coordinates (derived)", G_OBJ, [
            Col("id", "Joint", "text", editable=False),
            Col("x", "X", num, "length", editable=False),
            Col("y", "Y", num, "length", editable=False),
            Col("z", "Z", num, "length", editable=False),
            Col("story", "Story", "text", editable=False),
            Col("frames", "Frame ends", "int", editable=False),
            Col("shells", "Shell corners", "int", editable=False),
            Col("links", "Link ends", "int", editable=False),
            Col("support", "Support", "text", editable=False),
        ]))

    # ---- objects
    T.append(ObjectTable(
        "frame_objects", "Frame Objects", G_OBJ, [
            Col("uid", "Frame", "text"),
            Col("kind", "Type", "enum", enum=("column", "beam", "brace"),
                required=True),
            Col("section", "Section", "enum", enum=_names("sections"),
                required=True),
            Col("story", "Story", "enum", enum=_opt_stories, default=""),
            *_pt_cols("i", "pi", "I-End "),
            *_pt_cols("j", "pj", "J-End "),
            Col("angle", "Angle", num, "angle_deg", default=0.0),
            _release_col("Mi", "I (M2,M3)"),
            _release_col("Mj", "J (M2,M3)"),
            Col("cardinal_point", "Cardinal Pt", "int",
                enum=tuple(range(1, 12)), default=10),
            Col("end_offsets", "End Offsets", "enum", enum=END_OFFSET_MODES,
                default="manual"),
            Col("rigid_i", "Offset I", num, "length", default=0.0),
            Col("rigid_j", "Offset J", num, "length", default=0.0),
            Col("rigid_factor", "Rigid Factor", num, default=1.0),
            Col("axial_limit", "Axial Limit", "enum", enum=AXIAL_LIMITS,
                default="both"),
            Col("additional_mass", "Add. Mass", num, "mass_per_length",
                default=0.0),
            Col("length", "Length", num, "length", editable=False),
        ], container="members", kind="list", keycol="uid", auto_prefix="F",
        on_rename=_cascade_uid("members"), on_delete=_delete_uid("members"),
        new_obj=lambda d: {"uid": "", "story": "", "releases": "",
                           "angle": 0.0}))
    T.append(ObjectTable(
        "shell_objects", "Shell Objects", G_OBJ, [
            Col("uid", "Shell", "text"),
            Col("kind", "Type", "enum", enum=("wall", "slab"), required=True),
            Col("behavior", "Behavior", "enum", enum=("shell", "membrane"),
                required=True),
            Col("section", "Section", "enum", enum=_opt_names("shell_sections"),
                default=""),
            Col("story", "Story", "enum", enum=_opt_stories, default=""),
            Col("corners", "Corners", "points", "length", required=True),
            Col("mesh_size", "Mesh Size", num, "length", default=1.0),
            Col("pier", "Pier", "text", default=""),
            Col("wind_cp", "Wind Cp", num, optional=True),
            Col("diaphragm", "Diaphragm", "enum", enum=_opt_names("diaphragms"),
                default=""),
            Col("additional_mass", "Add. Mass", num, "mass_per_area",
                default=0.0),
            Col("area", "Area", num, "area", editable=False,
                get=lambda o, d: _poly_area(o.get("corners") or [])),
        ], container="shells", kind="list", keycol="uid", auto_prefix="A",
        on_rename=_cascade_uid("shells"), on_delete=_delete_uid("shells"),
        new_obj=lambda d: {"uid": "", "mesh_size": 1.0, "story": "",
                           "openings": [], "pier": "", "area_spring": None,
                           "wind_cp": None}))
    T.append(ObjectTable(
        "links", "Link Objects", G_OBJ, [
            Col("uid", "Link", "text"),
            Col("link_type", "Type", "enum",
                enum=_with_current(LINK_TYPES, ("links", "link_type")),
                default="elastic"),
            *_pt_cols("i", "pi", "I-End "),
            *_pt_cols("j", "pj", "J-End "),
            *_k6_cols(),
        ], container="links", kind="list", keycol="uid", auto_prefix="L",
        on_rename=_cascade_uid("links"), on_delete=_delete_uid("links"),
        new_obj=lambda d: {"uid": "", "stiffness": [0.0] * 6,
                           "link_type": "elastic", "params": {}}))

    # ---- assignments
    rcols = []
    for i, lb in enumerate(("UX", "UY", "UZ", "RX", "RY", "RZ")):
        def put(obj, v, d, i=i):
            r = list(obj.get("restraints") or [0] * 6)
            r[i] = 1 if v else 0
            obj["restraints"] = r
        rcols.append(Col(lb.lower(), lb, "bool", default=False,
                         get=lambda o, d, i=i: bool((o.get("restraints") or
                                                     [0] * 6)[i]), put=put))
    T.append(ObjectTable(
        "supports", "Joint Restraints (Supports)", G_ASN,
        [*_pt_cols("", "point", ""), *rcols],
        container="supports", kind="list",
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0], "restraints": [0] * 6}))

    def _put_prop(obj, v, d):
        if v:
            obj["property"] = v
        else:
            obj.pop("property", None)

    def _put_ang(obj, v, d):
        if v:
            obj["angle_deg"] = v
        else:
            obj.pop("angle_deg", None)
    T.append(ObjectTable(
        "point_springs", "Point Springs", G_ASN, [
            *_pt_cols("", "point", ""), *_k6_cols(),
            Col("property", "Property", "enum",
                enum=_opt_names("spring_properties"), default="",
                get=lambda o, d: o.get("property") or "", put=_put_prop),
            Col("angle_deg", "Angle", num, "angle_deg", default=0.0,
                get=lambda o, d: o.get("angle_deg", 0.0), put=_put_ang),
        ], container="spring_supports", kind="list",
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0], "stiffness": [0.0] * 6}))
    T.append(ObjectTable(
        "line_springs", "Line Springs", G_ASN, [
            *_pt_cols("1", "p1", "P1 "), *_pt_cols("2", "p2", "P2 "),
            Col("kx", "kx", num, "line_spring", default=0.0),
            Col("ky", "ky", num, "line_spring", default=0.0),
            Col("kz", "kz", num, "line_spring", default=0.0),
            Col("compression_only", "Compression Only", "bool", default=False),
        ], container="line_springs", kind="list",
        new_obj=lambda d: {"p1": [0.0] * 3, "p2": [0.0] * 3, "kx": 0.0,
                           "ky": 0.0, "kz": 0.0, "compression_only": False}))
    T.append(ObjectTable(
        "diaphragm_definitions", "Diaphragm Definitions", G_ASN, [
            Col("name", "Name", "text", required=True),
            Col("type", "Type", "enum", enum=DIAPHRAGM_TYPES, default="rigid"),
        ], container="diaphragms", keycol="name", name_in_obj=False,
        on_rename=_cascade_diaphragm,
        new_obj=lambda d: {"type": "rigid"}))
    T.append(StoryDiaphragmTable(
        "story_diaphragms", "Story Diaphragm Option", G_ASN, [
            Col("story", "Story", "text", editable=False),
            Col("diaphragm", "Option", "enum",
                enum=("default",) + tuple(DIAPHRAGM_OPTIONS)),
        ]))
    T.append(ObjectTable(
        "joint_diaphragms", "Joint Diaphragm Assignments", G_ASN, [
            *_pt_cols("", "point", ""),
            Col("diaphragm", "Diaphragm", "enum", enum=_names("diaphragms"),
                required=True),
        ], container="joint_diaphragms", kind="list",
        new_obj=lambda d: {"point": [0.0] * 3, "diaphragm": ""}))
    T.append(ObjectTable(
        "groups", "Group Definitions", G_ASN, [
            Col("name", "Group", "text", required=True),
            Col("color", "Color", "text", default=""),
            Col("n_members", "Frames", "int", editable=False,
                get=lambda o, d: len(o.get("members") or [])),
            Col("n_shells", "Shells", "int", editable=False,
                get=lambda o, d: len(o.get("shells") or [])),
            Col("n_links", "Links", "int", editable=False,
                get=lambda o, d: len(o.get("links") or [])),
            Col("n_points", "Points", "int", editable=False,
                get=lambda o, d: len(o.get("points") or [])),
        ], container="groups", keycol="name", name_in_obj=False,
        on_rename=_cascade_group,
        new_obj=lambda d: {"members": [], "shells": [], "links": [],
                           "points": [], "color": ""}))
    T.append(GroupAssignTable(
        "group_assignments", "Group Assignments", G_ASN, [
            Col("group", "Group", "enum", enum=_names("groups"), required=True),
            Col("type", "Object Type", "enum", enum=("frame", "shell", "link"),
                required=True),
            Col("object", "Object", "text", required=True),
        ]))

    # ---- loads
    T.append(ObjectTable(
        "load_patterns", "Load Pattern Definitions", G_LOAD, [
            Col("name", "Name", "text", required=True),
            Col("kind", "Type", "enum",
                enum=_with_current(PATTERN_KINDS, ("patterns", "kind")),
                default="other"),
            Col("self_weight_factor", "Self Wt Mult", num, default=0.0),
            Col("accidental_torsion", "Acc. Torsion", "bool", default=False),
            Col("ecc", "Ecc. Ratio", num, default=0.05),
        ], container="patterns", keycol="name",
        on_rename=_cascade_pattern, delete_refs=_pattern_refs,
        new_obj=lambda d: {"kind": "other", "member_udls": [],
                           "nodal_loads": [], "story_forces": [],
                           "member_loads": [], "area_loads": [],
                           "thermal_loads": [], "accidental_torsion": False,
                           "ecc": 0.05, "self_weight_factor": 0.0}))
    pat_col = Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                  required=True)
    T.append(PatternListTable(
        "joint_loads", "Joint Loads", G_LOAD, [
            pat_col, *_pt_cols("", "point", ""),
            *[Col(k, k.upper(), num, "force", default=0.0)
              for k in ("fx", "fy", "fz")],
            *[Col(k, k.upper(), num, "moment", default=0.0)
              for k in ("mx", "my", "mz")],
        ], lists=("nodal_loads",), target=_nodal_target,
        new_obj=lambda d: {"point": [0.0] * 3, "fx": 0.0, "fy": 0.0,
                           "fz": 0.0}))
    T.append(PatternListTable(
        "story_forces", "Story Lateral Forces", G_LOAD, [
            pat_col,
            Col("story", "Story", "enum", enum=_stories_enum, required=True),
            Col("fx", "FX", num, "force", default=0.0),
            Col("fy", "FY", num, "force", default=0.0),
        ], lists=("story_forces",),
        target=lambda lst, o: ("story_forces", {"story": o["story"],
                                                "fx": o.get("fx", 0.0),
                                                "fy": o.get("fy", 0.0)}),
        new_obj=lambda d: {"story": "", "fx": 0.0, "fy": 0.0}))
    T.append(PatternListTable(
        "frame_distributed_loads", "Frame Loads - Distributed", G_LOAD, [
            pat_col,
            Col("member_uid", "Frame", "ref", ref="members", required=True),
            Col("kind", "Shape", "enum", enum=DIST_KINDS, default="udl"),
            Col("direction", "Direction", "enum", enum=MEMBER_LOAD_DIRECTIONS,
                default="gravity"),
            Col("w", "w (start)", num, "line_force", required=True),
            Col("w2", "w (end)", num, "line_force", default=0.0),
            Col("a", "Rel. Dist. a", num, default=0.0),
            Col("b", "Rel. Dist. b", num, default=1.0),
            Col("projected", "Projected", "bool", default=False),
        ], lists=("member_udls", "member_loads"),
        belongs=lambda lst, e: lst == "member_udls" or e.get("kind", "udl")
        in DIST_KINDS,
        to_row_obj=_udl_as_load, target=_dist_target,
        new_obj=lambda d: {"member_uid": "", "kind": "udl", "w": 0.0,
                           "w2": 0.0, "a": 0.0, "b": 1.0,
                           "direction": "gravity"}))
    T.append(PatternListTable(
        "frame_point_loads", "Frame Loads - Point", G_LOAD, [
            pat_col,
            Col("member_uid", "Frame", "ref", ref="members", required=True),
            Col("direction", "Direction", "enum", enum=MEMBER_LOAD_DIRECTIONS,
                default="gravity"),
            Col("w", "P", num, "force", required=True),
            Col("a", "Rel. Dist.", num, default=0.5),
        ], lists=("member_loads",),
        belongs=lambda lst, e: e.get("kind") == "point",
        target=_ml_target,
        new_obj=lambda d: {"member_uid": "", "kind": "point", "w": 0.0,
                           "w2": 0.0, "a": 0.5, "b": 1.0,
                           "direction": "gravity"}))
    T.append(PatternListTable(
        "area_loads", "Shell Loads - Uniform", G_LOAD, [
            pat_col,
            Col("region_uid", "Shell", "ref", ref="shells", required=True),
            Col("q", "Load", num, "pressure", required=True),
            Col("direction", "Direction", "enum", enum=AREA_LOAD_DIRECTIONS,
                default="gravity"),
            Col("projected", "Projected", "bool", default=False),
        ], lists=("area_loads",), target=_area_target,
        new_obj=lambda d: {"region_uid": "", "q": 0.0}))

    # ---- load cases
    T.append(CaseSummaryTable(
        "load_cases", "Load Case Summary", G_CASE, [
            Col("name", "Case", "text", editable=False),
            Col("type", "Type", "text", editable=False),
            Col("run", "Run", "bool", editable=False),
        ]))
    T.append(ObjectTable(
        "static_cases", "Load Cases - Linear Static", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("geometric", "Geometric Nonlinearity", "enum",
                enum=GEOMETRIC_OPTIONS, default="linear"),
            Col("pdelta", "P-Delta (legacy)", "bool", default=False),
        ], container="cases", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs,
        new_obj=lambda d: {"patterns": {}, "pdelta": False,
                           "pdelta_gravity": None, "geometric": "linear"}))
    T.append(ChildDictTable(
        "static_case_loads", "Load Cases - Static Load Assignments", G_CASE, [
            Col("case", "Case", "enum", enum=_names("cases"), required=True),
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("factor", "Scale Factor", num, required=True),
        ], container="cases", field="patterns", parent_col="case",
        child_col="pattern", value_col="factor"))
    T.append(ObjectTable(
        "rs_cases", "Load Cases - Response Spectrum", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("direction", "Direction", "enum", enum=RS_DIRECTIONS,
                required=True),
            Col("function", "Function", "enum",
                enum=_opt_names("spectrum_functions"), default=""),
            Col("num_modes", "Modes (0=all)", "int", default=0),
            Col("combo_method", "Modal Combo", "enum", enum=RS_COMBO_METHODS,
                default="CQC"),
            Col("damping", "Damping", num, default=0.05),
            Col("scale", "Scale Factor", num, default=1.0),
        ], container="rs_cases", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs,
        new_obj=lambda d: {"spectrum": [], "num_modes": 0,
                           "combo_method": "CQC", "damping": 0.05,
                           "scale": 1.0, "function": ""}))
    T.append(ObjectTable(
        "th_cases", "Load Cases - Time History", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("direction", "Direction", "enum", enum=TH_DIRECTIONS,
                required=True),
            Col("function", "Function", "enum",
                enum=_opt_names("th_functions"), default=""),
            Col("dt", "Time Step", num, "period", default=0.0),
            Col("damping", "Damping", num, default=0.05),
            Col("scale", "Scale Factor", num, default=1.0),
            Col("nonlinear", "Nonlinear", "bool", default=False),
            Col("damping_model", "Damping Model", "enum",
                enum=TH_DAMPING_MODELS, default="rayleigh"),
        ], container="th_cases", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs,
        new_obj=lambda d: {"accel": [], "dt": 0.0, "damping": 0.05,
                           "scale": 1.0, "function": ""}))
    T.append(ObjectTable(
        "pushover_cases", "Load Cases - Pushover", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("direction", "Direction", "enum", enum=PUSHOVER_DIRECTIONS,
                required=True),
            Col("target_drift", "Target Drift", num, default=0.02),
            Col("steps", "Steps", "int", default=100),
            Col("hinges", "Hinges", "enum",
                enum=PUSHOVER_HINGE_MODES + ("asce41",),
                default="column_base"),
            Col("hardening", "Hardening", num, default=0.02),
            Col("geometric", "Geometric Nonlinearity", "enum",
                enum=GEOMETRIC_OPTIONS, default="linear"),
        ], container="pushover_cases", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs, new_obj=lambda d: {"gravity": {}}))
    T.append(ObjectTable(
        "staged_cases", "Load Cases - Staged Construction", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
        ], container="staged_cases", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs,
        new_obj=lambda d: {"stages": "per_story", "include_live": {}}))
    T.append(ObjectTable(
        "buckling_cases", "Load Cases - Buckling", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("num_modes", "Modes", "int", default=6),
            Col("base_case", "Base Case", "enum", enum=_opt_names("cases"),
                default="", get=lambda o, d: o.get("base_case") or "",
                put=lambda o, v, d: o.__setitem__("base_case", v or None)),
        ], container="buckling_cases", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs, new_obj=lambda d: {"gravity": {}}))
    T.append(ObjectTable(
        "load_combinations", "Load Combination Definitions", G_CASE, [
            Col("name", "Combination", "text", required=True),
            Col("combo_type", "Type", "enum", enum=COMBO_TYPES, default="add"),
        ], container="combos", keycol="name", on_rename=_cascade_case,
        delete_refs=_case_refs,
        new_obj=lambda d: {"cases": {}, "combo_type": "add"}))
    T.append(ChildDictTable(
        "combo_members", "Load Combination Members", G_CASE, [
            Col("combo", "Combination", "enum", enum=_names("combos"),
                required=True),
            Col("case", "Case / Combo", "enum", enum=_combo_member_pool,
                required=True),
            Col("factor", "Scale Factor", num, required=True),
        ], container="combos", field="cases", parent_col="combo",
        child_col="case", value_col="factor"))

    # ---- mass source
    T.append(ChildDictTable(
        "mass_source", "Mass Source - Load Patterns", G_DEF, [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("factor", "Multiplier", num, required=True),
        ], container=None, field="mass_source", child_col="pattern",
        value_col="factor"))
    T.append(MassOptionsTable(
        "mass_source_options", "Mass Source - Options", G_DEF, [
            Col("mode", "Self Mass Mode", "enum", enum=MASS_SOURCE_MODES),
            Col("self_mass", "Element Self Mass", "bool"),
            Col("patterns", "Additional Mass from Patterns", "bool"),
            Col("include_lateral", "Lateral Mass", "bool"),
            Col("include_vertical", "Vertical Mass", "bool"),
            Col("lump_at_stories", "Lump at Stories", "bool"),
        ]))
    return T


def _poly_area(corners) -> float:
    from skyframe.core.model import _polygon_area3d
    try:
        return float(_polygon_area3d([tuple(map(float, c)) for c in corners]))
    except (TypeError, ValueError, IndexError):
        return 0.0


_CATALOGUE: Optional[List[Table]] = None


def catalogue() -> List[Table]:
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = _build_catalogue()
    return _CATALOGUE


def table(key: str) -> Table:
    for t in catalogue():
        if t.key == key:
            return t
    raise KeyError(key)


def table_keys() -> List[str]:
    return [t.key for t in catalogue()]


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def list_tables(d: Optional[dict] = None) -> List[dict]:
    out = []
    for t in catalogue():
        m = t.meta(d or {})
        if d is not None:
            try:
                m["rows"] = len(t.rows(d))
            except Exception:            # pragma: no cover - defensive
                m["rows"] = None
        out.append(m)
    return out


def get_table(d: dict, key: str) -> dict:
    t = table(key)
    return {**t.meta(d), "columns": [c.meta(d) for c in t.cols],
            "rows": t.rows(d)}


def _locate(message: str, applied: Dict[str, list]) -> Tuple[Optional[str],
                                                              Optional[int],
                                                              Optional[str]]:
    """Best-effort attribution of a model-validation message to a row."""
    for key, rows in applied.items():
        t = table(key)
        idcols = [c.key for c in t.cols if c.key in (
            "name", "uid", "member_uid", "region_uid", "combo", "case",
            "group", "pattern", "story")]
        for col in idcols:
            for i, r in enumerate(rows):
                v = r.get(col) if isinstance(r, dict) else None
                if not isinstance(v, str) or len(v) < 1:
                    continue
                if re.search(r"(?<![\w-])" + re.escape(v) + r"(?![\w-])",
                             message):
                    return key, i, col
    return None, None, None


def _translate_rows(t: Table, rows: list, renamed: Dict[str, dict]) -> list:
    """Rows of a later table still name objects an earlier table of the
    same apply renamed: map them to the new names."""
    out = []
    pmap = renamed.get("patterns", {})
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        r = dict(r)
        for c in t.cols:
            if c.refc == "*cases":
                mp = {k: v for cc in _CASE_CONTAINERS
                      for k, v in renamed.get(cc, {}).items()}
            else:
                mp = renamed.get(c.refc) if c.refc else None
            if mp and isinstance(r.get(c.key), str) and r[c.key] in mp:
                r[c.key] = mp[r[c.key]]
        rid = _rid(r)
        if (isinstance(t, PatternListTable) and isinstance(rid, list)
                and rid and rid[0] in pmap):
            r["_id"] = [pmap[rid[0]]] + list(rid[1:])
        out.append(r)
    return out


def apply_tables(d: dict, tables: Dict[str, list]):
    """Apply edited tables atomically.

    Returns ``(model, errors, summary)``: ``model`` is the validated
    :class:`BuildingModel` (``None`` on any error) and ``errors`` a list of
    ``{table, row, col, message}``.
    """
    from skyframe.core.model import BuildingModel
    errs: List[dict] = []
    if not isinstance(tables, dict) or not tables:
        return None, [_err(None, None, None,
                           "'tables' must map table keys to row lists")], {}
    work = copy.deepcopy(d)
    order = {k: i for i, k in enumerate(table_keys())}
    summary = {}
    renamed: Dict[str, Dict[str, str]] = {}
    for key in sorted(tables, key=lambda k: order.get(k, 1e9)):
        if key not in order:
            errs.append(_err(key, None, None, f"unknown table {key!r}"))
            continue
        rows = tables[key]
        if not isinstance(rows, list):
            errs.append(_err(key, None, None, "rows must be a list"))
            continue
        t = table(key)
        if renamed:
            rows = _translate_rows(t, rows, renamed)
        summary[key] = t.apply(work, rows, errs)
        for old, new in (summary[key] or {}).get("renamed", {}).items():
            renamed.setdefault(t.container, {})[old] = new
    if errs:
        return None, errs, {}
    try:
        model = BuildingModel.from_dict(work)
    except (ValueError, KeyError, TypeError) as exc:
        msg = str(exc) if not isinstance(exc, KeyError) else \
            f"missing field {exc}"
        tk, row, col = _locate(msg, tables)
        return None, [_err(tk, row, col, msg)], {}
    return model, [], summary


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #
def _csv_cell(col_type, v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if col_type == "points" and isinstance(v, list):
        return "; ".join(",".join(repr(float(c)) for c in p) for p in v)
    if isinstance(v, float):
        return repr(v)
    return str(v)


def table_to_csv(d: dict, key: str, include_id: bool = True) -> str:
    t = table(key)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    cols = t.cols
    w.writerow((["_id"] if include_id else []) + [c.key for c in cols])
    for r in t.rows(d):
        w.writerow(([json.dumps(r.get("_id"), separators=(",", ":"))]
                    if include_id else [])
                   + [_csv_cell(c.type, r.get(c.key)) for c in cols])
    return buf.getvalue()


_UNIT_SUFFIX = re.compile(r"\s*\[[^\]]*\]\s*$")


def csv_to_rows(d: dict, key: str, text: str) -> List[dict]:
    """Parse a table CSV back into rows (headers = column keys or labels,
    optionally suffixed ``[unit]``; values in SI)."""
    t = table(key)
    if text.startswith("﻿"):
        text = text[1:]
    rd = csv.reader(io.StringIO(text))
    try:
        header = next(rd)
    except StopIteration:
        return []
    by = {}
    for c in t.cols:
        by[c.key] = c
        by[c.label.lower()] = c
    keys = []
    for h in header:
        h0 = _UNIT_SUFFIX.sub("", h).strip()
        if h0 == "_id":
            keys.append("_id")
        else:
            c = by.get(h0) or by.get(h0.lower())
            keys.append(c.key if c else None)
    rows = []
    for rec in rd:
        if not any(s.strip() for s in rec):
            continue
        r = {}
        for k, s in zip(keys, rec):
            if k is None:
                continue
            if k == "_id":
                if s.strip():
                    try:
                        r["_id"] = json.loads(s)
                    except ValueError:
                        r["_id"] = s
                continue
            c = by[k]
            if not c.editable and c.key != t.keycol:
                continue
            r[k] = _csv_value(c, s)
        rows.append(r)
    return rows


def _csv_value(c: Col, s: str):
    s = s.strip()
    if c.type in ("number", "int"):
        if not s:
            return None
        try:
            v = float(s)
            return int(v) if c.type == "int" and v == int(v) else v
        except ValueError:
            return s                        # reported by apply
    if c.type == "bool":
        if s.lower() in _TRUE:
            return True
        if s.lower() in _FALSE:
            return False
        return s
    return s


# --------------------------------------------------------------------------- #
# whole-model export / import (zip of CSVs, one per table)
# --------------------------------------------------------------------------- #
def model_signature(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def export_zip(d: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        index = io.StringIO()
        w = csv.writer(index, lineterminator="\n")
        w.writerow(["table", "title", "group", "editable", "rows"])
        for t in catalogue():
            rows = t.rows(d)
            w.writerow([t.key, t.title, t.group, t.editable, len(rows)])
            z.writestr(f"{t.key}.csv", table_to_csv(d, t.key))
        z.writestr("_index.csv", index.getvalue())
        z.writestr("_meta.json", json.dumps({
            "format": "skyframe-modeltables", "version": 1,
            "units": "SI (kN, m, kPa, tonne, degC)",
            "model_sha256": model_signature(d)}))
    return buf.getvalue()


def import_zip(d: dict, data: bytes):
    """Apply every editable table found in the zip (catalogue order,
    atomically).  ``_id`` columns are honoured only when the zip was
    exported from this very model (else rows match by key / are new)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None, [_err(None, None, None, "not a zip archive")], {}
    with z:
        names = set(z.namelist())
        same = False
        if "_meta.json" in names:
            try:
                meta = json.loads(z.read("_meta.json").decode("utf-8"))
                same = meta.get("model_sha256") == model_signature(d)
            except ValueError:
                same = False
        tables = {}
        for t in catalogue():
            fn = f"{t.key}.csv"
            if fn not in names or not t.editable:
                continue
            rows = csv_to_rows(d, t.key, z.read(fn).decode("utf-8-sig"))
            if not same:
                for r in rows:
                    r.pop("_id", None)
            tables[t.key] = rows
    if not tables:
        return None, [_err(None, None, None,
                           "the archive contains no editable table CSV")], {}
    return apply_tables(d, tables)
