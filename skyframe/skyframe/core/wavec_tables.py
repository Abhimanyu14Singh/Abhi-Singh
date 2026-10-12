"""Wave C Interactive Database Editing coverage (one hook line in
``modeltables._build_catalogue``):

* ``shell_objects`` gains the floor auto-mesh columns (``Floor Mesh``
  default | rectangular | cookie_cut | none, ``Floor Max Size``, ``Mesh at
  Beams / Walls / Grids``) and ``Edge Constraint`` (model | yes | no);
* ``pier_assignments`` -- Pier Label Assignments (columns and walls);
* ``load_pattern_ecc_overrides`` -- Load Pattern Eccentricity Overrides
  (pattern, story, diaphragm, ratio | length -- exactly one per row).

Untouched rows apply as the identity: a column whose cell equals the
stored value is never written, and a legacy shell reads ``floor_mesh``
blank / flags True / ``Edge Constraint`` "model".  The max-size and flag
cells are stored only while ``Floor Mesh`` names a mode (blank = no
floor_mesh options; the cells are then ignored).
"""

from __future__ import annotations

from typing import List

from . import edge_constraints_ext as _ece
from .modeltables import Col, Table, _err, _names, parse_cell, RowError

num = "number"
FM_MODES = ("", "default", "rectangular", "cookie_cut", "none")
_FLAGS = ("at_beams", "at_walls", "at_grids")


def _fm_get(k):
    def g(o, d):
        fm = o.get("floor_mesh")
        if k == "mode":
            return (fm or {}).get("mode", "default") if fm else ""
        if k == "max_size":
            return (fm or {}).get("max_size")
        return bool((fm or {}).get(k, True))
    return g


def _fm_put(k):
    def p(o, v, d):
        fm = o.get("floor_mesh")
        if k == "mode":
            if v in (None, ""):
                o.pop("floor_mesh", None)
                return
            fm = dict(fm or {})
            fm["mode"] = v
            o["floor_mesh"] = fm
            return
        if fm is None:
            return       # no Floor Mesh mode: max size / flags not stored
        fm = dict(fm)
        fm[k] = v
        o["floor_mesh"] = fm
    return p


def _shell_columns(T: List[Table]) -> None:
    so = next(t for t in T if t.key == "shell_objects")
    i = next(k for k, c in enumerate(so.cols) if c.key == "area")
    so.cols = so.cols[:i] + [
        Col("floor_mesh", "Floor Mesh", "enum", enum=FM_MODES, default="",
            get=_fm_get("mode"), put=_fm_put("mode")),
        Col("floor_mesh_max", "Floor Max Size", num, "length",
            optional=True, get=_fm_get("max_size"),
            put=_fm_put("max_size")),
        Col("mesh_at_beams", "Mesh at Beams", "bool", default=True,
            get=_fm_get("at_beams"), put=_fm_put("at_beams")),
        Col("mesh_at_walls", "Mesh at Walls", "bool", default=True,
            get=_fm_get("at_walls"), put=_fm_put("at_walls")),
        Col("mesh_at_grids", "Mesh at Grids", "bool", default=True,
            get=_fm_get("at_grids"), put=_fm_put("at_grids")),
        Col("edge_constraint", "Edge Constraint", "enum",
            enum=_ece.EC_CHOICES, default="model", get=_ece.col_get,
            put=_ece.col_put),
    ] + so.cols[i:]


# --------------------------------------------------------------------------- #
# Load Pattern Eccentricity Overrides
# --------------------------------------------------------------------------- #
class EccOverridesTable(Table):
    """One row per (pattern, story[/diaphragm]) override; Apply rewrites
    every pattern's ``ecc_overrides`` from the rows (patterns without rows
    lose the key)."""

    def _items(self, d):
        for pname, p in (d.get("patterns") or {}).items():
            for k, v in (p.get("ecc_overrides") or {}).items():
                yield pname, k, v

    def rows(self, d):
        out = []
        for pname, k, v in self._items(d):
            st, _, dia = str(k).partition("/")
            out.append({"_id": [pname, k], "pattern": pname, "story": st,
                        "diaphragm": dia,
                        "ratio": None if isinstance(v, dict) else v,
                        "length": v.get("length") if isinstance(v, dict)
                        else None})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        pats = d.get("patterns") or {}
        new = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            vals = {}
            for c in self.cols:
                try:
                    vals[c.key] = parse_cell(c, row.get(c.key, c.default), d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
            if len(errs) > n0:
                continue
            pn = vals["pattern"]
            if pn not in pats:
                errs.append(_err(self.key, i, "pattern",
                                 f"unknown load pattern {pn!r}"))
                continue
            st = str(vals["story"] or "").strip()
            dia = str(vals.get("diaphragm") or "").strip()
            key = f"{st}/{dia}" if dia else st
            r_, l_ = vals.get("ratio"), vals.get("length")
            if (r_ is None) == (l_ is None):
                errs.append(_err(self.key, i, "ratio", "give either a "
                                 "Ratio or a Length (exactly one)"))
                continue
            val = float(r_) if l_ is None else {"length": float(l_)}
            slot = new.setdefault(pn, {})
            if key in slot:
                errs.append(_err(self.key, i, "story",
                                 f"{pn}: override {key!r} is listed twice"))
                continue
            slot[key] = val
        if len(errs) > n0:
            return {}
        changed = 0
        for pn, p in pats.items():
            cur = p.get("ecc_overrides") or {}
            nv = new.get(pn) or {}
            if cur != nv:
                changed += 1
                if nv:
                    p["ecc_overrides"] = nv
                else:
                    p.pop("ecc_overrides", None)
        return {"rows": len(rows), "changed": changed}


def _stories(d):
    return [s.get("name") for s in d.get("stories") or []]


def extend(T: List[Table]) -> None:
    """Hook called once by ``modeltables._build_catalogue``."""
    from .frame_piers import modeltable as _pier_table
    _shell_columns(T)
    T.append(_pier_table())
    T.append(EccOverridesTable(
        "load_pattern_ecc_overrides", "Load Pattern Eccentricity Overrides",
        "Model Definition > Loads", [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("story", "Story", "enum", enum=_stories, required=True),
            Col("diaphragm", "Diaphragm", "text", default=""),
            Col("ratio", "Ecc. Ratio", num, optional=True),
            Col("length", "Ecc. Length", num, "length", optional=True),
        ]))

