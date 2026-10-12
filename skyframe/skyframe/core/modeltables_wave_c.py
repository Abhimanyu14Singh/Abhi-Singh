"""Wave C Interactive Database Editing coverage.

One hook line at the end of ``modeltables._build_catalogue`` calls
:func:`extend`, which

* appends the time-dependent columns to "Load Cases - Staged
  Construction" (one column per ``time_dependent`` key, blank = absent)
  plus a read-only stage-mode column;
* appends new tables (all editable, all round-trip byte-identically one
  by one, all together, through CSV and through the zip workbook):

  ==================================  ======================================
  key                                 model data
  ==================================  ======================================
  staged_stages                       ``staged_cases[*].stages`` (list form)
  staged_operations                   every stage operation (add / remove /
                                      load + age_days / pattern / scale)
  staged_live                         ``staged_cases[*].include_live``
  hyperstatic_cases                   ``hyperstatic_cases``
  section_cuts                        ``section_cuts``
  frame_temperature_loads             pattern ``thermal_loads``
  shell_temperature_loads             pattern ``shell_thermal_loads``
  joint_temperatures                  pattern ``joint_temperatures``
  ground_displacements                pattern ``ground_displacements``
  joint_patterns                      ``joint_patterns``
  pdelta_options / _load_factors      ``pdelta_options``
  active_dof                          ``active_dof``
  analysis_options / _convergence_    ``analysis_options``
  tests
  damping_options                     ``damping_options``
  panel_zone_option                   ``panel_zones``
  joint_panel_zones                   ``joint_panel_zones``
  towers / joint_towers               ``towers`` / ``joint_towers``
  nonprismatic_segments /             ``sections[kind=nonprismatic]``
  nonprismatic_subdivisions
  builtup_sections / _components      ``builtup_sections`` (+ mirror)
  designer_sections / _polygons /     ``designer_sections`` (+ mirror)
  _rebar
  frame_hinge_assignments             ``members[*].hinges``
  frame_hinge_overwrites              ``members[*].hinge_overwrites``
  floor_mesh                          ``shells[*].floor_mesh``
  generalized_displacements / _terms  ``generalized_displacements``
  ==================================  ======================================

Options tables have exactly one row and write only the values that
differ from the effective (normalized) value; a key returning to its
default is removed again, so model -> rows -> apply is the identity.
Numbers inside text cells (vertices, dims, lists) use :func:`jsnum`, the
JavaScript ``Number#toString`` form, so the ?mock=1 port produces the
same text.  Built-up and Section Designer edits re-derive the mirrored
frame section exactly like ``add_builtup_section`` /
``add_designer_section`` (modifiers kept).
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

from . import modeltables as MT
from .modeltables import (Col, RowError, Table, ObjectTable,
                          PatternListTable, ChildDictTable, _err, _rid,
                          _same, _names, _opt_names, parse_cell,
                          _cascade_case, _case_refs, _cascade_section,
                          _pt_cols)
from .modeltables_ext import ListChildTable, _same_json, _chain

num = "number"
G_DEF = "Model Definition > Properties"
G_OBJ = "Model Definition > Objects"
G_ASN = "Model Definition > Assignments"
G_LOAD = "Model Definition > Loads"
G_CASE = "Model Definition > Load Cases & Combinations"
G_AOPT = "Model Definition > Analysis Options"

MT.QUANTITIES.setdefault("temp_delta", "degC")
MT.QUANTITIES.setdefault("temp_gradient", "degC/m")
DOF6 = ("UX", "UY", "UZ", "RX", "RY", "RZ")


# --------------------------------------------------------------------------- #
# number text (JavaScript Number#toString, so the mock prints the same)
# --------------------------------------------------------------------------- #
def jsnum(v) -> str:
    v = float(v)
    if not math.isfinite(v):
        return "NaN" if v != v else ("Infinity" if v > 0 else "-Infinity")
    if v == 0.0:
        return "0"
    if v.is_integer() and abs(v) < 1e21:
        return str(int(v))
    sign = "-" if v < 0 else ""
    r = repr(abs(v))
    mant, e = (r.split("e") + ["0"])[:2]
    e = int(e)
    ip, fp = (mant.split(".") + [""])[:2]
    allds = ip + fp
    lead = len(allds) - len(allds.lstrip("0"))
    digits = allds.lstrip("0").rstrip("0") or "0"
    n = len(ip) + e - lead
    k = len(digits)
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    ex = n - 1
    es = ("+" if ex >= 0 else "-") + str(abs(ex))
    if k == 1:
        return sign + digits + "e" + es
    return sign + digits[0] + "." + digits[1:] + "e" + es


def _num_text(vals) -> str:
    return ", ".join(jsnum(v) for v in vals)


def _parse_nums(s) -> List[float]:
    out = []
    for p in re.split(r"[,;\s]+", str(s or "").strip()):
        if not p:
            continue
        try:
            x = float(p)
        except ValueError:
            raise ValueError(f"{p!r} is not a number") from None
        if not math.isfinite(x):
            raise ValueError(f"{p!r} is not finite")
        out.append(x)
    return out


def _text_list(s) -> List[str]:
    return [p.strip() for p in re.split(r"[,;]", str(s or "")) if p.strip()]


def _put_opt(key):
    """put(): store ``v`` or REMOVE the key for blank / None."""
    def put(obj, v, d):
        if v is None or v == "":
            obj.pop(key, None)
        else:
            obj[key] = v
    return put


def _put_default(key, default):
    def put(obj, v, d):
        if v == default or v is None:
            obj.pop(key, None)
        else:
            obj[key] = v
    return put


# --------------------------------------------------------------------------- #
# one-row options tables
# --------------------------------------------------------------------------- #
class OptionsTable(Table):
    """Exactly one row; ``read(d)`` gives the effective values, ``write(d,
    changed)`` stores only the changed ones."""

    can_add = False
    can_delete = False
    read = None
    write = None

    def rows(self, d):
        return [{"_id": 0, **{c.key: v for c, v in
                              zip(self.cols, self._vals(d))}}]

    def _vals(self, d):
        cur = self.read(d)
        return [cur.get(c.key) for c in self.cols]

    def apply(self, d, rows, errs):
        if len(rows) != 1 or not isinstance(rows[0], dict):
            errs.append(_err(self.key, None, None,
                             f"the {self.title} table has exactly one row"))
            return {}
        n0 = len(errs)
        cur = self.read(d)
        changed = {}
        for c in self.cols:
            if not c.editable or c.key not in rows[0]:
                continue
            raw = rows[0][c.key]
            if type(raw) is type(cur.get(c.key)) and _same(raw,
                                                           cur.get(c.key)):
                continue
            try:
                v = parse_cell(c, raw, d)
            except RowError as exc:
                errs.append(_err(self.key, 0, c.key, f"{c.label}: {exc}"))
                continue
            if not _same(v, cur.get(c.key)):
                changed[c.key] = v
        if len(errs) > n0:
            return {}
        if changed:
            try:
                self.write(d, changed)
            except ValueError as exc:
                errs.append(_err(self.key, 0, None, str(exc)))
                return {}
        return {"rows": 1, "changed": sorted(changed)}


# ---- P-Delta
def _pd_read(d):
    from .pdelta_options import normalized
    return normalized(d.get("pdelta_options"))


def _pd_store(d, o):
    from .pdelta_options import pdelta_defaults
    if o == pdelta_defaults():
        d.pop("pdelta_options", None)
    else:
        d["pdelta_options"] = {
            "method": o["method"],
            "load_factors": {k: float(v)
                             for k, v in o["load_factors"].items()},
            "max_iterations": o["max_iterations"],
            "tolerance": float(o["tolerance"]),
            "include_in": o["include_in"]}


def _pd_write(d, changed):
    o = _pd_read(d)
    o.update(changed)
    _pd_store(d, o)


class PDeltaFactorsTable(Table):
    keycol = None

    def rows(self, d):
        lf = _pd_read(d)["load_factors"]
        return [{"_id": [p], "pattern": p, "factor": f} for p, f in
                lf.items()]

    def apply(self, d, rows, errs):
        n0 = len(errs)
        built: Dict[str, float] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            try:
                p = parse_cell(self.col("pattern"), row.get("pattern"), d)
                f = parse_cell(self.col("factor"), row.get("factor"), d)
            except RowError as exc:
                col = "pattern" if "pattern" not in row or not isinstance(
                    row.get("pattern"), str) or row["pattern"].strip() not in \
                    (d.get("patterns") or {}) else "factor"
                errs.append(_err(self.key, i, col, str(exc)))
                continue
            if p in built:
                errs.append(_err(self.key, i, "pattern",
                                 f"duplicate Load Pattern {p!r}"))
                continue
            built[p] = f
        if len(errs) > n0:
            return {}
        o = _pd_read(d)
        old = o["load_factors"]
        if list(old) == list(built) and all(_same(old[k], built[k])
                                            for k in old):
            return {"rows": len(rows)}
        o["load_factors"] = MT._merge_ordered(old, built)
        _pd_store(d, o)
        return {"rows": len(rows)}


# ---- active DOF
def _dof_read(d):
    act = set(d.get("active_dof") or DOF6)
    return {k: k in act for k in DOF6}


def _dof_write(d, changed):
    cur = _dof_read(d)
    cur.update(changed)
    d["active_dof"] = [k for k in DOF6 if cur[k]]


# ---- analysis options
def _ao_read(d):
    from .analysis_options import normalized
    o = normalized(d.get("analysis_options") or {})
    conv = o["convergence"]
    par = o["parallel"]
    return {"solver": o["solver"], "eigen_solver": o["eigen_solver"],
            "size_warnings": bool(o["size_warnings"]),
            "parallel": str(par) if not isinstance(par, str) else par,
            "conv_mode": conv["mode"],
            "algorithms": ", ".join(conv["algorithms"]),
            "max_halvings": conv["max_halvings"]}


def _ao_store(d, stored):
    from .analysis_options import normalized, defaults
    if not stored or normalized(stored) == defaults():
        d.pop("analysis_options", None)
    else:
        d["analysis_options"] = stored


def _ao_write(d, changed):
    stored = copy.deepcopy(d.get("analysis_options") or {})
    conv = dict(stored.get("convergence") or {})
    for k, v in changed.items():
        if k == "parallel":
            s = str(v).strip()
            if s in ("auto", "off"):
                stored["parallel"] = s
            else:
                try:
                    stored["parallel"] = int(s)
                except ValueError:
                    raise ValueError("Parallel: use auto, off or 1..8") \
                        from None
        elif k == "conv_mode":
            conv["mode"] = v
        elif k == "algorithms":
            conv["algorithms"] = _text_list(v)
        elif k == "max_halvings":
            conv["max_halvings"] = v
        else:
            stored[k] = v
    if conv:
        stored["convergence"] = conv
    _ao_store(d, stored)


class ConvTestsTable(Table):
    keycol = None

    def rows(self, d):
        from .analysis_options import normalized
        tests = normalized(d.get("analysis_options") or {})[
            "convergence"]["tests"]
        return [{"_id": k, "type": t.get("type"),
                 "tolerance": t.get("tolerance"),
                 "max_iterations": t.get("max_iterations")}
                for k, t in enumerate(tests)]

    def apply(self, d, rows, errs):
        from .analysis_options import normalized
        n0 = len(errs)
        built = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            e = {}
            for c in self.cols:
                try:
                    v = parse_cell(c, row.get(c.key), d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
                    break
                if v is not None:
                    e[c.key] = v
            built.append(e)
        if len(errs) > n0:
            return {}
        cur = normalized(d.get("analysis_options") or {})["convergence"][
            "tests"]
        if _same_json_num(cur, built):
            return {"rows": len(rows)}
        stored = copy.deepcopy(d.get("analysis_options") or {})
        conv = dict(stored.get("convergence") or {})
        conv["tests"] = built
        stored["convergence"] = conv
        _ao_store(d, stored)
        return {"rows": len(rows)}


def _same_json_num(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same_json_num(a[k], b[k])
                                            for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same_json_num(x, y)
                                        for x, y in zip(a, b))
    return _same(a, b)


# ---- damping options
def _dmp_read(d):
    from .damping_options import normalized
    o = normalized(d.get("damping_options"))
    return {"rayleigh_on_links": o["rayleigh_on_links"],
            "rayleigh_stiffness_basis": o["rayleigh_stiffness_basis"]}


def _dmp_write(d, changed):
    from .damping_options import damping_defaults
    o = _dmp_read(d)
    o.update(changed)
    if o == damping_defaults():
        d.pop("damping_options", None)
    else:
        d["damping_options"] = o


# ---- panel zones (model-wide)
def _pz_read(d):
    return {"panel_zones": d.get("panel_zones", "none")}


def _pz_write(d, changed):
    d["panel_zones"] = changed["panel_zones"]


# --------------------------------------------------------------------------- #
# staged construction
# --------------------------------------------------------------------------- #
_TD = ("days_per_story", "creep_coeff", "shrinkage", "aging", "t_eval",
       "materials", "method", "steps_per_stage", "output_times")


def _td_get(key):
    def g(o, d):
        td = o.get("time_dependent") or {}
        v = td.get(key)
        if key == "aging":
            return "" if v is None else ("true" if v else "false")
        if key == "materials":
            return "" if v is None else ", ".join(str(x) for x in v)
        if key == "output_times":
            return "" if v is None else _num_text(v)
        if key == "method":
            return "" if v is None else str(v)
        return v
    return g


def _td_put(key):
    def p(o, v, d):
        td = dict(o.get("time_dependent") or {})
        if v is None or v == "":
            td.pop(key, None)
        elif key == "aging":
            td[key] = (v == "true")
        elif key == "materials":
            td[key] = _text_list(v)
        elif key == "output_times":
            td[key] = _parse_nums(v)
        else:
            td[key] = v
        o["time_dependent"] = td or None
    return p


def _stage_mode(o, d):
    st = o.get("stages", "per_story")
    return st if isinstance(st, str) else f"user ({len(st)})"


def _staged_cols() -> List[Col]:
    from .timedep import STAGED_METHODS
    return [
        Col("stage_mode", "Stages", "text", editable=False, get=_stage_mode),
        Col("td_days_per_story", "TD Days per Story", num, optional=True,
            get=_td_get("days_per_story"), put=_td_put("days_per_story")),
        Col("td_creep_coeff", "TD Creep Coeff", num, optional=True,
            get=_td_get("creep_coeff"), put=_td_put("creep_coeff")),
        Col("td_shrinkage", "TD Shrinkage", num, optional=True,
            get=_td_get("shrinkage"), put=_td_put("shrinkage")),
        Col("td_aging", "TD Aging", "enum", enum=("", "true", "false"),
            default="", get=_td_get("aging"), put=_td_put("aging")),
        Col("td_t_eval", "TD Eval Time (days)", num, optional=True,
            get=_td_get("t_eval"), put=_td_put("t_eval")),
        Col("td_materials", "TD Materials", "text", default="",
            get=_td_get("materials"), put=_td_put("materials")),
        Col("td_method", "TD Method", "enum",
            enum=("",) + tuple(STAGED_METHODS), default="",
            get=_td_get("method"), put=_td_put("method")),
        Col("td_steps_per_stage", "TD Steps per Stage", "int",
            optional=True, get=_td_get("steps_per_stage"),
            put=_td_put("steps_per_stage")),
        Col("td_output_times", "TD Output Times", "text", default="",
            get=_td_get("output_times"), put=_td_put("output_times")),
    ]


def _user_stages(sc) -> List[dict]:
    st = sc.get("stages")
    return st if isinstance(st, list) else []


class StagedStagesTable(Table):
    """Rows = the stages of every user-defined staged case (``_id`` =
    [case, index]).  Deleting every stage of a case returns it to
    ``"per_story"``; operations of a kept stage are untouched."""

    def rows(self, d):
        out = []
        for cn, sc in (d.get("staged_cases") or {}).items():
            for k, st in enumerate(_user_stages(sc)):
                out.append({"_id": [cn, k], "case": cn,
                            "stage": st.get("name"),
                            "duration_days": st.get("duration_days", 0.0)})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        cases = d.get("staged_cases") or {}
        built: Dict[str, list] = {}
        seen = set()
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            cn = row.get("case")
            cn = cn.strip() if isinstance(cn, str) else cn
            if cn not in cases:
                errs.append(_err(self.key, i, "case",
                                 f"unknown staged case {cn!r}"))
                continue
            rid = _rid(row)
            orig = None
            if (isinstance(rid, list) and len(rid) == 2 and rid[0] == cn
                    and isinstance(rid[1], int)
                    and 0 <= rid[1] < len(_user_stages(cases[cn]))
                    and (cn, rid[1]) not in seen):
                orig = _user_stages(cases[cn])[rid[1]]
                seen.add((cn, rid[1]))
            try:
                name = parse_cell(self.col("stage"), row.get("stage"), d)
                dur = parse_cell(self.col("duration_days"),
                                 row.get("duration_days", 0.0), d)
            except RowError as exc:
                errs.append(_err(self.key, i, None, str(exc)))
                continue
            if not name:
                errs.append(_err(self.key, i, "stage", "Stage is required"))
                continue
            if orig is not None:
                st = orig
                if st.get("name") != name or not _same(
                        st.get("duration_days", 0.0), dur):
                    st = dict(orig)
                    st["name"] = name
                    if not _same(orig.get("duration_days", 0.0), dur):
                        st["duration_days"] = dur
            else:
                st = {"name": name, "duration_days": dur, "operations": []}
            built.setdefault(cn, []).append(st)
        if len(errs) > n0:
            return {}
        changed = 0
        for cn, sc in cases.items():
            old = _user_stages(sc)
            new = built.get(cn, [])
            if len(old) == len(new) and all(a is b for a, b in zip(old,
                                                                   new)):
                continue
            sc["stages"] = new if new else "per_story"
            changed += 1
        return {"rows": len(rows), "cases_changed": changed}


_OP_ORDER = {"add": ("op", "group", "age_days"),
             "remove": ("op", "group"),
             "load": ("op", "group", "pattern", "scale")}


class StagedOpsTable(Table):
    """Rows = every operation of every stage (``_id`` = [case, stage
    index, op index]); columns that do not apply to an operation are blank
    (age_days: add only, pattern / scale: load only)."""

    def rows(self, d):
        out = []
        for cn, sc in (d.get("staged_cases") or {}).items():
            for k, st in enumerate(_user_stages(sc)):
                for j, op in enumerate(st.get("operations") or []):
                    out.append({"_id": [cn, k, j], "case": cn,
                                "stage": st.get("name"),
                                "op": op.get("op"),
                                "group": op.get("group"),
                                "age_days": op.get("age_days"),
                                "pattern": op.get("pattern") or "",
                                "scale": op.get("scale")})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        cases = d.get("staged_cases") or {}
        built: Dict[Tuple[str, int], list] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            cn = row.get("case")
            cn = cn.strip() if isinstance(cn, str) else cn
            stages = _user_stages(cases[cn]) if cn in cases else None
            if not stages:
                errs.append(_err(self.key, i, "case",
                                 f"{cn!r} is not a staged case with "
                                 "user-defined stages"))
                continue
            sname = row.get("stage")
            sname = sname.strip() if isinstance(sname, str) else sname
            idx = next((k for k, st in enumerate(stages)
                        if st.get("name") == sname), None)
            if idx is None:
                errs.append(_err(self.key, i, "stage",
                                 f"unknown stage {sname!r} of {cn!r}"))
                continue
            vals = {}
            bad = False
            for c in self.cols:
                if c.key in ("case", "stage"):
                    continue
                try:
                    vals[c.key] = parse_cell(c, row.get(c.key), d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
                    bad = True
            if bad:
                continue
            kind = vals["op"]
            if not vals.get("group"):
                errs.append(_err(self.key, i, "group", "Group is required"))
                continue
            if kind == "load" and not vals.get("pattern"):
                errs.append(_err(self.key, i, "pattern",
                                 "a load operation needs a Load Pattern"))
                continue
            op = {}
            for k in _OP_ORDER[kind]:
                v = vals.get(k)
                if v is None or v == "":
                    continue
                op[k] = v
            rid = _rid(row)
            if isinstance(rid, list) and len(rid) == 3 and rid[0] == cn \
                    and rid[1] == idx:
                ops0 = stages[idx].get("operations") or []
                if isinstance(rid[2], int) and 0 <= rid[2] < len(ops0) \
                        and _same_json_num(ops0[rid[2]], op):
                    op = ops0[rid[2]]
            built.setdefault((cn, idx), []).append(op)
        if len(errs) > n0:
            return {}
        changed = 0
        for cn, sc in cases.items():
            for k, st in enumerate(_user_stages(sc)):
                old = st.get("operations") or []
                new = built.get((cn, k), [])
                if len(old) == len(new) and all(a is b for a, b in
                                                 zip(old, new)):
                    continue
                st["operations"] = new
                changed += 1
        return {"rows": len(rows), "stages_changed": changed}


# --------------------------------------------------------------------------- #
# pattern-list load tables
# --------------------------------------------------------------------------- #
def _thermal_obj(lst, e):
    out = copy.deepcopy(e)
    out.setdefault("grad2", 0.0)
    out.setdefault("grad3", 0.0)
    return out


def _thermal_target(lst, obj):
    out = {"member_uid": obj["member_uid"], "dT": obj.get("dT", 0.0)}
    for k in ("grad2", "grad3"):
        if obj.get(k):
            out[k] = obj[k]
    return "thermal_loads", out


def _shell_thermal_target(lst, obj):
    return "shell_thermal_loads", {"region_uid": obj["region_uid"],
                                   "dT": obj.get("dT", 0.0),
                                   "grad3": obj.get("grad3", 0.0)}


def _joint_temp_target(lst, obj):
    return "joint_temperatures", {"point": list(obj["point"]),
                                  "dT": obj.get("dT", 0.0)}


def _ground_target(lst, obj):
    out = {"point": list(obj["point"])}
    for k in ("ux", "uy", "uz", "rx", "ry", "rz"):
        out[k] = obj.get(k, 0.0)
    return "ground_displacements", out


# --------------------------------------------------------------------------- #
# cascades
# --------------------------------------------------------------------------- #
def _cascade_joint_pattern(d, mp):
    for p in (d.get("patterns") or {}).values():
        for a in p.get("area_loads") or []:
            if isinstance(a.get("joint_pattern"), str) and \
                    a["joint_pattern"] in mp:
                a["joint_pattern"] = mp[a["joint_pattern"]]


def _joint_pattern_refs(d, gone):
    for pn, p in (d.get("patterns") or {}).items():
        for a in p.get("area_loads") or []:
            if a.get("joint_pattern") in gone:
                return (f"joint pattern {a['joint_pattern']!r} is used by an "
                        f"area load of pattern {pn!r}")
    return None


def _tower_users(d):
    for s in d.get("stories") or []:
        yield s, f"story {s.get('name')!r}"
    for m in d.get("members") or []:
        yield m, f"frame {m.get('uid')!r}"
    for r in d.get("shells") or []:
        yield r, f"shell {r.get('uid')!r}"
    for j in d.get("joint_towers") or []:
        yield j, f"joint {j.get('point')}"


def _cascade_tower(d, mp):
    for o, _w in _tower_users(d):
        if o.get("tower") in mp:
            o["tower"] = mp[o["tower"]]
    for k in ("pushover_cases", "nonlinear_static_cases"):
        for c in (d.get(k) or {}).values():
            if c.get("control_tower") in mp:
                c["control_tower"] = mp[c["control_tower"]]


def _tower_refs(d, gone):
    for o, what in _tower_users(d):
        if o.get("tower") in gone:
            return f"tower {o['tower']!r} is used by {what}"
    return None


def _towers_enum(d):
    return [t.get("name") for t in d.get("towers") or []]


_towers_enum.refc = "towers"


def _grids_opt(d):
    return [""] + [g.get("name") for g in (d.get("grid_systems") or [])]


def _np_names(d):
    return [n for n, s in (d.get("sections") or {}).items()
            if s.get("kind") == "nonprismatic"]


_np_names.refc = "sections"


def _prismatic_names(d):
    return [n for n, s in (d.get("sections") or {}).items()
            if s.get("kind", "prismatic") != "nonprismatic"]


_prismatic_names.refc = "sections"


def _hinge_targets(d):
    from .model import MEMBER_HINGE_OPTIONS
    return list(MEMBER_HINGE_OPTIONS[1:]) + list(
        (d.get("hinge_properties") or {}).keys())


# --------------------------------------------------------------------------- #
# mirrored sections (built-up / Section Designer)
# --------------------------------------------------------------------------- #
_KEEP = ("mod_A", "mod_I33", "mod_I22", "mod_J", "As2", "As3",
         "shear_deformation", "mod_As2", "mod_As3", "mod_mass", "mod_weight")


def _materials(d):
    from .model import material_from_dict
    return {n: material_from_dict(md, n)
            for n, md in (d.get("materials") or {}).items()}


def remirror(d, kind: str, names) -> None:
    """Re-derive the mirrored FrameSection of built-up (``kind`` =
    "builtup") or designer sections ``names`` (modifiers kept)."""
    if not names:
        return
    mats = _materials(d)
    secs = d.setdefault("sections", {})
    for name in names:
        if kind == "builtup":
            from .builtup import (BuiltupSection, resolve_components,
                                  validate_builtup_section,
                                  make_frame_section)
            bs = BuiltupSection.from_dict(d["builtup_sections"][name])
            bs.name = name
            resolve_components(bs)
            validate_builtup_section(bs, mats)
            sec = make_frame_section(bs, mats).to_dict()
        else:
            from .sections_designer import (DesignerSection,
                                            validate_designer_section,
                                            make_frame_section)
            ds = DesignerSection.from_dict(d["designer_sections"][name])
            ds.name = name
            validate_designer_section(ds, mats)
            sec = make_frame_section(ds, mats).to_dict()
        old = secs.get(name)
        if old is not None:
            for k in _KEEP:
                if k in old:
                    sec[k] = old[k]
        secs[name] = sec


class MirrorObjectTable(ObjectTable):
    """Built-up / designer definitions: changed or new definitions
    re-derive their mirrored frame section; renames rename it; deletes
    remove it (refused while a frame uses it)."""

    mirror = ""

    def apply(self, d, rows, errs):
        before = copy.deepcopy(d.get(self.container) or {})
        n0 = len(errs)
        out = super().apply(d, rows, errs)
        if len(errs) > n0:
            return out
        ren = (out or {}).get("renamed") or {}
        inv = {v: k for k, v in ren.items()}
        changed = [n for n, o in (d.get(self.container) or {}).items()
                   if not _same_json(before.get(inv.get(n, n)), o)]
        try:
            remirror(d, self.mirror, changed)
        except (ValueError, KeyError, TypeError) as exc:
            errs.append(_err(self.key, None, None, str(exc)))
            return {}
        return out


def _mirror_rename(d, mp):
    secs = d.get("sections") or {}
    d["sections"] = {mp.get(k, k): v for k, v in secs.items()}
    for k, v in d["sections"].items():
        if k in mp.values():
            v["name"] = k
    _cascade_section(d, mp)


def _mirror_refs(d, gone):
    for m in d.get("members") or []:
        if m.get("section") in gone:
            return (f"section {m['section']!r} is used by frame "
                    f"{m['uid']!r}")
    return None


def _mirror_delete(d, gone):
    for n in gone:
        (d.get("sections") or {}).pop(n, None)


class MirrorChildTable(ListChildTable):
    mirror = ""

    def apply(self, d, rows, errs):
        before = copy.deepcopy(d.get(self.container) or {})
        n0 = len(errs)
        out = super().apply(d, rows, errs)
        if len(errs) > n0:
            return out
        changed = [n for n, o in (d.get(self.container) or {}).items()
                   if not _same_json(before.get(n), o)]
        try:
            remirror(d, self.mirror, changed)
        except (ValueError, KeyError, TypeError) as exc:
            errs.append(_err(self.key, None, None, str(exc)))
            return {}
        return out


def _dims_get(o, d):
    dims = o.get("dims")
    if not isinstance(dims, dict):
        return ""
    return "; ".join(f"{k}={v if isinstance(v, str) else jsnum(v)}"
                     for k, v in dims.items())


def _dims_put(o, v, d):
    s = str(v or "").strip()
    if not s:
        o.pop("dims", None)
        return
    dims = {}
    for part in re.split(r"[;,]", s):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"dims entry {part!r} must be key=value")
        k, val = (x.strip() for x in part.split("=", 1))
        try:
            dims[k] = float(val)
        except ValueError:
            dims[k] = val
    o["dims"] = dims


def _verts_get(o, d):
    return "; ".join(f"{jsnum(p[0])},{jsnum(p[1])}"
                     for p in o.get("vertices") or [])


def _verts_put(o, v, d):
    pts = []
    for chunk in str(v or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        xs = _parse_nums(chunk)
        if len(xs) != 2:
            raise ValueError("every vertex needs exactly 2 coordinates y,z")
        pts.append(xs)
    if len(pts) < 3:
        raise ValueError("a polygon needs at least 3 vertices 'y,z; y,z; ...'")
    o["vertices"] = pts


# --------------------------------------------------------------------------- #
# frame hinge assignments / overwrites / floor mesh
# --------------------------------------------------------------------------- #
class HingeAssignTable(Table):
    """Rows = the hinges of every frame: a mode string (auto_m3 /
    fiber_pmm) as one row with blank Rel Dist, or one row per
    user-property hinge.  A frame without a row has hinges "none"."""

    def rows(self, d):
        out = []
        for m in d.get("members") or []:
            h = m.get("hinges", "none")
            if isinstance(h, str):
                if h != "none":
                    out.append({"_id": [m["uid"], 0], "frame": m["uid"],
                                "hinge": h, "relative_distance": None})
            else:
                for k, e in enumerate(h):
                    out.append({"_id": [m["uid"], k], "frame": m["uid"],
                                "hinge": e.get("property"),
                                "relative_distance":
                                    e.get("relative_distance")})
        return out

    def apply(self, d, rows, errs):
        from .model import MEMBER_HINGE_OPTIONS
        n0 = len(errs)
        built: Dict[str, list] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            vals = {}
            bad = False
            for c in self.cols:
                try:
                    vals[c.key] = parse_cell(c, row.get(c.key), d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
                    bad = True
            if bad:
                continue
            if not vals["frame"]:
                errs.append(_err(self.key, i, "frame", "Frame is required"))
                continue
            built.setdefault(vals["frame"], []).append(
                (i, vals["hinge"], vals["relative_distance"]))
        if len(errs) > n0:
            return {}
        for m in d.get("members") or []:
            ents = built.get(m["uid"])
            cur = m.get("hinges", "none")
            if not ents:
                new = "none"
            elif len(ents) == 1 and ents[0][1] in MEMBER_HINGE_OPTIONS:
                if ents[0][2] is not None:
                    errs.append(_err(self.key, ents[0][0],
                                     "relative_distance",
                                     f"{ents[0][1]!r} hinges sit at both "
                                     "ends (leave Rel Dist blank)"))
                    continue
                new = ents[0][1]
            else:
                new = []
                for i, h, rd in ents:
                    if h in MEMBER_HINGE_OPTIONS or rd is None:
                        errs.append(_err(self.key, i, "relative_distance",
                                         "a hinge-property row needs a "
                                         "property and a Rel Dist; "
                                         f"{h!r} cannot be mixed"))
                        break
                    new.append({"property": h, "relative_distance": rd})
            if not _same_json_num(cur, new):
                m["hinges"] = new
        if len(errs) > n0:
            return {}
        return {"rows": len(rows)}


class MemberAttrTable(Table):
    """Rows = members (or shells) whose optional dict attribute ``attr`` is
    set; a missing row clears it (``None``)."""

    container = "members"
    attr = ""
    objcol = "frame"
    build = None          # (vals, old) -> dict

    def rows(self, d):
        out = []
        for o in d.get(self.container) or []:
            v = o.get(self.attr)
            if v is None:
                continue
            r = {"_id": o["uid"], self.objcol: o["uid"]}
            for c in self.cols:
                if c.key != self.objcol:
                    r[c.key] = c.get(v, d)
            out.append(r)
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        built: Dict[str, dict] = {}
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                errs.append(_err(self.key, i, None, "row must be an object"))
                continue
            vals = {}
            bad = False
            for c in self.cols:
                try:
                    vals[c.key] = parse_cell(c, row.get(c.key, c.default), d)
                except RowError as exc:
                    errs.append(_err(self.key, i, c.key, f"{c.label}: {exc}"))
                    bad = True
            if bad:
                continue
            u = vals[self.objcol]
            if not u:
                errs.append(_err(self.key, i, self.objcol,
                                 f"{self.col(self.objcol).label} is "
                                 "required"))
                continue
            if u in built:
                errs.append(_err(self.key, i, self.objcol,
                                 f"{u!r} is listed twice"))
                continue
            built[u] = vals
        if len(errs) > n0:
            return {}
        for o in d.get(self.container) or []:
            old = o.get(self.attr)
            vals = built.get(o["uid"])
            if vals is None:
                if old is not None:
                    o[self.attr] = None
                continue
            new = self.build(vals, old)
            if old is None or not _same_json_num(old, new):
                o[self.attr] = new
        return {"rows": len(rows)}


def _ov_build(vals, old):
    out = {}
    if vals.get("auto_subdivide") is not None and (
            vals["auto_subdivide"] or old is None
            or "auto_subdivide" in (old or {})):
        out["auto_subdivide"] = bool(vals["auto_subdivide"])
    if vals.get("relative_length") is not None:
        out["relative_length"] = vals["relative_length"]
    return out


def _fm_build(vals, old):
    return {"mode": vals["mode"], "max_size": vals["max_size"],
            "at_beams": vals["at_beams"], "at_walls": vals["at_walls"],
            "at_grids": vals["at_grids"]}


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
def _range_col(axis, idx, label):
    key = f"{axis}_range"

    def get(o, d):
        r = o.get(key)
        return None if r is None else r[idx]

    def put(o, v, d):
        r = list(o.get(key) or [None, None])
        r[idx] = v
        o[key] = None if r == [None, None] else r
    return Col(f"{axis}_{'min' if idx == 0 else 'max'}", label, num,
               "length", optional=True, get=get, put=put)


def _gd_terms_pt(i):
    def put(o, v, d):
        p = list(o.get("point") or [0.0, 0.0, 0.0])
        p[i] = v
        o["point"] = p
    return put


def extend(T: List[Table]) -> None:
    """Hook called once by ``modeltables._build_catalogue``."""
    from .model import PANEL_ZONE_OPTIONS
    from .pdelta_options import PDELTA_METHODS, PDELTA_INCLUDE_IN
    from .analysis_options import (SOLVERS, EIGEN_SOLVERS,
                                   CONVERGENCE_MODES, TESTS)
    from .damping_options import (RAYLEIGH_ON_LINKS,
                                  RAYLEIGH_STIFFNESS_BASES)
    from .panelzones import PZ_PROPERTIES, PZ_CONNECTIVITY
    from .nonprismatic import LENGTH_TYPES, EI_VARIATIONS
    from .builtup import AXES, TORSION, COMPONENT_KINDS
    from .sections_designer import DESIGNER_BASES, DESIGNER_TORSION
    from .shellopts import FLOOR_MESH_MODES
    from .groups import STAGE_OPS
    from .gendisp import GD_TYPES, GD_DOFS

    # ---- staged construction: time-dependent columns + stages / ops
    stg = next(t for t in T if t.key == "staged_cases")
    stg.cols = list(stg.cols) + _staged_cols()
    T.append(StagedStagesTable(
        "staged_stages", "Load Cases - Staged Construction - Stages", G_CASE, [
            Col("case", "Case", "enum", enum=_names("staged_cases"),
                required=True),
            Col("stage", "Stage", "text", required=True),
            Col("duration_days", "Duration (days)", num, default=0.0),
        ]))
    T.append(StagedOpsTable(
        "staged_operations", "Load Cases - Staged Construction - Operations",
        G_CASE, [
            Col("case", "Case", "enum", enum=_names("staged_cases"),
                required=True),
            Col("stage", "Stage", "text", required=True),
            Col("op", "Operation", "enum", enum=tuple(STAGE_OPS),
                required=True),
            Col("group", "Group", "enum", enum=_opt_names("groups"),
                required=True),
            Col("age_days", "Age at Add (days)", num, optional=True),
            Col("pattern", "Load Pattern", "enum",
                enum=_opt_names("patterns"), default=""),
            Col("scale", "Scale Factor", num, optional=True),
        ]))
    T.append(ChildDictTable(
        "staged_live", "Load Cases - Staged Construction - Final Live Loads",
        G_CASE, [
            Col("case", "Case", "enum", enum=_names("staged_cases"),
                required=True),
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("factor", "Scale Factor", num, required=True),
        ], container="staged_cases", field="include_live",
        parent_col="case", child_col="pattern", value_col="factor"))
    T.append(ObjectTable(
        "hyperstatic_cases", "Load Cases - Hyperstatic", G_CASE, [
            Col("name", "Case", "text", required=True),
            Col("case", "Linear Static Case", "enum", enum=_names("cases"),
                required=True),
        ], container="hyperstatic_cases", keycol="name", name_in_obj=False,
        on_rename=_cascade_case, delete_refs=_case_refs,
        new_obj=lambda d: {"case": ""}))
    T.append(ObjectTable(
        "section_cuts", "Section Cut Definitions", G_CASE, [
            Col("name", "Name", "text", required=True),
            Col("axis", "Axis", "enum", enum=("x", "y", "z"), default="z"),
            Col("coord", "Coordinate", num, "length", required=True),
            _range_col("x", 0, "X Min"), _range_col("x", 1, "X Max"),
            _range_col("y", 0, "Y Min"), _range_col("y", 1, "Y Max"),
            _range_col("z", 0, "Z Min"), _range_col("z", 1, "Z Max"),
            Col("group", "Group", "enum", enum=_opt_names("groups"),
                default="", get=lambda o, d: o.get("group") or "",
                put=_put_opt("group")),
        ], container="section_cuts", kind="list", keycol="name",
        new_obj=lambda d: {"name": "", "axis": "z", "coord": 0.0,
                           "x_range": None, "y_range": None,
                           "z_range": None}))

    # ---- loads
    T.append(PatternListTable(
        "frame_temperature_loads", "Frame Loads - Temperature", G_LOAD, [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("member_uid", "Frame", "ref", ref="members", required=True),
            Col("dT", "Temperature Change", num, "temp_delta",
                required=True),
            Col("grad2", "Gradient 2", num, "temp_gradient", default=0.0),
            Col("grad3", "Gradient 3", num, "temp_gradient", default=0.0),
        ], lists=("thermal_loads",), to_row_obj=_thermal_obj,
        target=_thermal_target,
        new_obj=lambda d: {"member_uid": "", "dT": 0.0, "grad2": 0.0,
                           "grad3": 0.0}))
    T.append(PatternListTable(
        "shell_temperature_loads", "Shell Loads - Temperature", G_LOAD, [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("region_uid", "Shell", "ref", ref="shells", required=True),
            Col("dT", "Temperature Change", num, "temp_delta",
                required=True),
            Col("grad3", "Gradient 3", num, "temp_gradient", default=0.0),
        ], lists=("shell_thermal_loads",),
        target=_shell_thermal_target,
        new_obj=lambda d: {"region_uid": "", "dT": 0.0, "grad3": 0.0}))
    T.append(PatternListTable(
        "joint_temperatures", "Joint Loads - Temperature", G_LOAD, [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            *_pt_cols("", "point", "Joint "),
            Col("dT", "Temperature", num, "temp_delta", required=True),
        ], lists=("joint_temperatures",),
        target=_joint_temp_target,
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0], "dT": 0.0}))
    T.append(PatternListTable(
        "ground_displacements", "Joint Loads - Ground Displacement", G_LOAD, [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            *_pt_cols("", "point", "Joint "),
            Col("ux", "UX", num, "length", default=0.0),
            Col("uy", "UY", num, "length", default=0.0),
            Col("uz", "UZ", num, "length", default=0.0),
            Col("rx", "RX (rad)", num, default=0.0),
            Col("ry", "RY (rad)", num, default=0.0),
            Col("rz", "RZ (rad)", num, default=0.0),
        ], lists=("ground_displacements",),
        target=_ground_target,
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0]}))
    T.append(ObjectTable(
        "joint_patterns", "Joint Patterns", G_LOAD, [
            Col("name", "Name", "text", required=True),
            Col("type", "Type", "text", editable=False,
                get=lambda o, d: o.get("type", "linear")),
            Col("a", "A (x)", num, default=0.0),
            Col("b", "B (y)", num, default=0.0),
            Col("c", "C (z)", num, default=0.0),
            Col("d", "D (constant)", num, default=0.0),
            Col("zero_negative", "Zero Negative Values", "bool",
                default=False, put=_put_default("zero_negative", False)),
            Col("zero_positive", "Zero Positive Values", "bool",
                default=False, put=_put_default("zero_positive", False)),
        ], container="joint_patterns", keycol="name", name_in_obj=False,
        on_rename=_cascade_joint_pattern, delete_refs=_joint_pattern_refs,
        new_obj=lambda d: {"type": "linear", "a": 0.0, "b": 0.0, "c": 0.0,
                           "d": 0.0}))

    # ---- analysis options
    T.append(OptionsTable(
        "pdelta_options", "P-Delta Options", G_AOPT, [
            Col("method", "Method", "enum", enum=PDELTA_METHODS),
            Col("max_iterations", "Max Iterations", "int"),
            Col("tolerance", "Relative Tolerance", num),
            Col("include_in", "Include In", "enum", enum=PDELTA_INCLUDE_IN),
        ], read=lambda d: {
            k: v for k, v in _pd_read(d).items() if k != "load_factors"},
        write=_pd_write))
    T.append(PDeltaFactorsTable(
        "pdelta_load_factors", "P-Delta Options - Load Combination", G_AOPT, [
            Col("pattern", "Load Pattern", "enum", enum=_names("patterns"),
                required=True),
            Col("factor", "Scale Factor", num, required=True),
        ]))
    T.append(OptionsTable(
        "active_dof", "Active Degrees of Freedom", G_AOPT,
        [Col(k, k, "bool") for k in DOF6],
        read=_dof_read, write=_dof_write))
    T.append(OptionsTable(
        "analysis_options", "Analysis Options - Solver and Convergence",
        G_AOPT, [
            Col("solver", "Solver", "enum", enum=SOLVERS),
            Col("eigen_solver", "Eigen Solver", "enum", enum=EIGEN_SOLVERS),
            Col("size_warnings", "Model-Size Warnings", "bool"),
            Col("parallel", "Parallel (auto / off / 1..8)", "text"),
            Col("conv_mode", "Convergence Mode", "enum",
                enum=CONVERGENCE_MODES),
            Col("algorithms", "Algorithms", "text"),
            Col("max_halvings", "Max Step Halvings", "int"),
        ], read=_ao_read, write=_ao_write))
    T.append(ConvTestsTable(
        "analysis_convergence_tests",
        "Analysis Options - Convergence Tests", G_AOPT, [
            Col("type", "Test", "enum", enum=TESTS, required=True),
            Col("tolerance", "Tolerance", num, optional=True),
            Col("max_iterations", "Max Iterations", "int", optional=True),
        ]))
    T.append(OptionsTable(
        "damping_options", "Damping Options - Links and Springs", G_AOPT, [
            Col("rayleigh_on_links", "Rayleigh on Links", "enum",
                enum=RAYLEIGH_ON_LINKS),
            Col("rayleigh_stiffness_basis", "Stiffness Basis", "enum",
                enum=RAYLEIGH_STIFFNESS_BASES),
        ], read=_dmp_read, write=_dmp_write))
    T.append(OptionsTable(
        "panel_zone_option", "Panel Zones - Model-Wide", G_ASN, [
            Col("panel_zones", "Panel Zones", "enum",
                enum=PANEL_ZONE_OPTIONS),
        ], read=_pz_read, write=_pz_write))

    # ---- assignments
    T.append(ObjectTable(
        "joint_panel_zones", "Joint Assignments - Panel Zones", G_ASN, [
            *_pt_cols("", "point", "Joint "),
            Col("property", "Property", "enum", enum=PZ_PROPERTIES,
                default="from_column"),
            Col("k", "Spring K", num, "rot_stiffness", optional=True,
                put=_put_opt("k")),
            Col("doubler_t", "Doubler Thickness", num, "dim", optional=True,
                put=_put_opt("doubler_t")),
            Col("connectivity", "Connectivity", "enum",
                enum=tuple(PZ_CONNECTIVITY), default="beams_to_panel"),
        ], container="joint_panel_zones", kind="list",
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0],
                           "property": "from_column"}))
    T.append(ObjectTable(
        "towers", "Tower Definitions", G_DEF, [
            Col("name", "Tower", "text", required=True),
            Col("grid_system", "Grid System", "enum", enum=_grids_opt,
                default="", get=lambda o, d: o.get("grid_system") or "",
                put=lambda o, v, d: o.__setitem__("grid_system", v or None)),
            Col("base_elevation", "Base Elevation", num, "length",
                default=0.0),
            Col("stories", "Stories", "text", editable=False,
                get=lambda o, d: ", ".join(o.get("stories") or [])),
        ], container="towers", kind="list", keycol="name",
        on_rename=_cascade_tower, delete_refs=_tower_refs,
        new_obj=lambda d: {"name": "", "grid_system": None,
                           "base_elevation": 0.0, "stories": []}))
    T.append(ObjectTable(
        "joint_towers", "Joint Assignments - Tower", G_ASN, [
            *_pt_cols("", "point", "Joint "),
            Col("tower", "Tower", "enum", enum=_towers_enum, required=True),
        ], container="joint_towers", kind="list",
        new_obj=lambda d: {"point": [0.0, 0.0, 0.0], "tower": ""}))
    T.append(HingeAssignTable(
        "frame_hinge_assignments", "Frame Assignments - Hinges", G_ASN, [
            Col("frame", "Frame", "ref", ref="members", required=True),
            Col("hinge", "Hinge (mode or property)", "enum",
                enum=_hinge_targets, required=True),
            Col("relative_distance", "Rel Dist", num, optional=True),
        ]))
    T.append(MemberAttrTable(
        "frame_hinge_overwrites", "Frame Assignments - Hinge Overwrites",
        G_ASN, [
            Col("frame", "Frame", "ref", ref="members", required=True),
            Col("auto_subdivide", "Auto Subdivide", "bool", default=False,
                get=lambda o, d: bool(o.get("auto_subdivide", False))),
            Col("relative_length", "Relative Length", num, optional=True,
                get=lambda o, d: o.get("relative_length")),
        ], container="members", attr="hinge_overwrites", objcol="frame",
        build=_ov_build))
    T.append(MemberAttrTable(
        "floor_mesh", "Shell Assignments - Floor Auto Mesh Options", G_ASN, [
            Col("shell", "Shell", "ref", ref="shells", required=True),
            Col("mode", "Mesh Option", "enum", enum=FLOOR_MESH_MODES,
                default="default", get=lambda o, d: o.get("mode",
                                                          "default")),
            Col("max_size", "Max Element Size", num, "length",
                optional=True, get=lambda o, d: o.get("max_size")),
            Col("at_beams", "At Beams", "bool", default=True,
                get=lambda o, d: bool(o.get("at_beams", True))),
            Col("at_walls", "At Walls", "bool", default=True,
                get=lambda o, d: bool(o.get("at_walls", True))),
            Col("at_grids", "At Grids", "bool", default=True,
                get=lambda o, d: bool(o.get("at_grids", True))),
        ], container="shells", attr="floor_mesh", objcol="shell",
        build=_fm_build))

    # ---- section properties: nonprismatic, built-up, Section Designer
    T.append(NPSegmentsTable(
        "nonprismatic_segments", "Frame Sections - Nonprismatic Segments",
        G_DEF, [
            Col("section", "Section", "enum", enum=_np_names, required=True),
            Col("start_section", "Start Section", "enum",
                enum=_prismatic_names, required=True),
            Col("end_section", "End Section", "enum", enum=_prismatic_names,
                required=True),
            Col("length", "Length", num, default=1.0),
            Col("length_type", "Length Type", "enum", enum=LENGTH_TYPES,
                default="relative"),
            Col("EI33", "EI33 Variation", "enum",
                enum=tuple(EI_VARIATIONS), default="linear"),
            Col("EI22", "EI22 Variation", "enum",
                enum=tuple(EI_VARIATIONS), default="linear"),
        ], container="sections", parent_col="section", field="segments",
        entry="dict",
        new_entry=lambda d: {"start_section": "", "end_section": "",
                             "length": 1.0, "length_type": "relative",
                             "EI33": "linear", "EI22": "linear"}))
    T.append(NPSubdivTable(
        "nonprismatic_subdivisions",
        "Frame Sections - Nonprismatic Subdivisions", G_DEF, [
            Col("section", "Section", "text", editable=False),
            Col("subdivisions", "Sub-elements per Varying Segment", "int",
                default=16),
        ]))
    T.append(MirrorObjectTable(
        "builtup_sections", "Frame Sections - Built-Up", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("material", "Material", "enum", enum=_names("materials"),
                required=True),
            Col("axes", "Analysis Axes", "enum", enum=AXES,
                default="geometric"),
            Col("torsion", "Torsion", "enum", enum=TORSION, default="auto"),
            Col("template", "Template", "text", editable=False,
                get=lambda o, d: (o.get("template") or {}).get("id", "")
                if isinstance(o.get("template"), dict) else ""),
            Col("notes", "Notes", "text", default="",
                put=_put_opt("notes")),
            Col("n_components", "Components", "int", editable=False,
                get=lambda o, d: len(o.get("components") or [])),
        ], container="builtup_sections", keycol="name", mirror="builtup",
        on_rename=_mirror_rename, delete_refs=_mirror_refs,
        on_delete=_mirror_delete,
        new_obj=lambda d: {"material": "", "components": [],
                           "axes": "geometric", "torsion": "auto"}))
    T.append(MirrorChildTable(
        "builtup_components", "Frame Sections - Built-Up Components", G_DEF, [
            Col("section", "Section", "enum",
                enum=_names("builtup_sections"), required=True),
            Col("kind", "Kind", "enum", enum=COMPONENT_KINDS,
                required=True),
            Col("type", "Shape Type", "text", default="",
                put=_put_opt("type")),
            Col("standard", "Database", "text", default="",
                put=_put_opt("standard")),
            Col("db_name", "Database Shape", "text", path="name",
                default="", put=_put_opt("name")),
            Col("dims", "Dimensions (key=value)", "text", default="",
                get=_dims_get, put=_dims_put),
            Col("b", "Plate b", num, "dim", optional=True,
                put=_put_opt("b")),
            Col("t", "Plate t", num, "dim", optional=True,
                put=_put_opt("t")),
            Col("x", "X", num, "dim", default=0.0),
            Col("y", "Y", num, "dim", default=0.0),
            Col("angle", "Plate Angle", num, "angle_deg", optional=True,
                put=_put_opt("angle")),
            Col("rotation", "Rotation", num, "angle_deg", optional=True,
                put=_put_opt("rotation")),
            Col("mirror", "Mirror", "bool", default=False,
                put=lambda o, v, d: o.__setitem__("mirror", v)
                if v or "mirror" in o else None),
            Col("material", "Material", "enum", enum=_opt_names("materials"),
                default="", get=lambda o, d: o.get("material") or "",
                put=_put_opt("material")),
            Col("label", "Label", "text", default="",
                get=lambda o, d: o.get("label") or "",
                put=_put_opt("label")),
        ], container="builtup_sections", parent_col="section",
        field="components", entry="dict", mirror="builtup",
        new_entry=lambda d: {"kind": "plate", "x": 0.0, "y": 0.0}))
    T.append(MirrorObjectTable(
        "designer_sections", "Frame Sections - Section Designer", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("material", "Base Material", "enum",
                enum=_names("materials"), required=True),
            Col("base", "Base", "enum", enum=DESIGNER_BASES,
                default="concrete"),
            Col("torsion", "Torsion", "enum", enum=DESIGNER_TORSION,
                default="approx", get=lambda o, d: o.get("torsion",
                                                         "approx"),
                put=_put_default("torsion", "approx")),
            Col("n_polygons", "Polygons", "int", editable=False,
                get=lambda o, d: len(o.get("polygons") or [])),
            Col("n_rebar", "Rebar", "int", editable=False,
                get=lambda o, d: len(o.get("rebar") or [])),
        ], container="designer_sections", keycol="name", mirror="designer",
        on_rename=_mirror_rename, delete_refs=_mirror_refs,
        on_delete=_mirror_delete,
        new_obj=lambda d: {"material": "", "base": "concrete",
                           "polygons": [], "rebar": []}))
    T.append(MirrorChildTable(
        "designer_polygons", "Frame Sections - Section Designer Polygons",
        G_DEF, [
            Col("section", "Section", "enum",
                enum=_names("designer_sections"), required=True),
            Col("material", "Material", "enum", enum=_names("materials"),
                required=True),
            Col("hole", "Hole", "bool", default=False),
            Col("vertices", "Vertices (y,z; ...)", "text", required=True,
                get=_verts_get, put=_verts_put),
        ], container="designer_sections", parent_col="section",
        field="polygons", entry="dict", mirror="designer",
        new_entry=lambda d: {"vertices": [], "material": "",
                             "hole": False}))
    T.append(MirrorChildTable(
        "designer_rebar", "Frame Sections - Section Designer Rebar", G_DEF, [
            Col("section", "Section", "enum",
                enum=_names("designer_sections"), required=True),
            Col("y", "Y", num, "dim", required=True),
            Col("z", "Z", num, "dim", required=True),
            Col("area", "Bar Area", num, "area", required=True),
            Col("material", "Material", "enum", enum=_names("materials"),
                required=True),
        ], container="designer_sections", parent_col="section",
        field="rebar", entry="dict", mirror="designer",
        new_entry=lambda d: {"y": 0.0, "z": 0.0, "area": 0.0,
                             "material": ""}))

    # ---- generalized displacements
    T.append(ObjectTable(
        "generalized_displacements", "Generalized Displacements", G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("type", "Type", "enum", enum=GD_TYPES,
                default="translation"),
            Col("n_terms", "Terms", "int", editable=False,
                get=lambda o, d: len(o.get("terms") or [])),
        ], container="generalized_displacements", keycol="name",
        name_in_obj=False,
        new_obj=lambda d: {"type": "translation", "terms": []}))
    T.append(ListChildTable(
        "generalized_displacement_terms", "Generalized Displacements - Terms",
        G_DEF, [
            Col("name", "Generalized Displacement", "enum",
                enum=_names("generalized_displacements"), required=True),
            Col("x", "Joint X", num, "length", path=("point", 0),
                required=True, put=_gd_terms_pt(0)),
            Col("y", "Joint Y", num, "length", path=("point", 1),
                required=True, put=_gd_terms_pt(1)),
            Col("z", "Joint Z", num, "length", path=("point", 2),
                required=True, put=_gd_terms_pt(2)),
            Col("dof", "DOF", "enum", enum=GD_DOFS, required=True),
            Col("sf", "Scale Factor", num, default=1.0),
        ], container="generalized_displacements", parent_col="name",
        field="terms", entry="dict",
        new_entry=lambda d: {"point": [0.0, 0.0, 0.0], "dof": "UX",
                             "sf": 1.0}))


class NPSegmentsTable(ListChildTable):
    """Segments of the nonprismatic sections only."""

    def _parents(self, d):
        return [(n, s) for n, s in (d.get("sections") or {}).items()
                if s.get("kind") == "nonprismatic"]


class NPSubdivTable(Table):
    can_add = False
    can_delete = False
    keycol = "section"

    def rows(self, d):
        return [{"_id": n, "section": n,
                 "subdivisions": s.get("subdivisions", 16)}
                for n, s in (d.get("sections") or {}).items()
                if s.get("kind") == "nonprismatic"]

    def apply(self, d, rows, errs):
        n0 = len(errs)
        secs = d.get("sections") or {}
        seen = set()
        for i, row in enumerate(rows):
            n = row.get("section", row.get("_id")) if isinstance(
                row, dict) else None
            if n not in secs or secs[n].get("kind") != "nonprismatic":
                errs.append(_err(self.key, i, "section",
                                 f"{n!r} is not a nonprismatic section"))
                continue
            if n in seen:
                errs.append(_err(self.key, i, "section",
                                 f"duplicate section {n!r}"))
                continue
            seen.add(n)
            if "subdivisions" not in row:
                continue
            try:
                v = parse_cell(self.col("subdivisions"),
                               row["subdivisions"], d)
            except RowError as exc:
                errs.append(_err(self.key, i, "subdivisions", str(exc)))
                continue
            if not _same(v, secs[n].get("subdivisions", 16)):
                secs[n]["subdivisions"] = v
        return {"rows": len(rows)} if len(errs) == n0 else {}
