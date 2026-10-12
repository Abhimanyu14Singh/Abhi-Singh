"""Interactive Database Editing coverage for time-dependent materials and
tendon elements (CONTRACT "Time-dependent materials, incremental creep and
tendon elements").  One hook line in ``modeltables_ext.extend_catalogue``
calls :func:`extend`, which

* inserts the editable table ``material_time_dependent`` ("Material
  Properties - Time Dependent", one row per material that has
  time-dependent properties: law, RH, h0, curing age, cement, curing,
  fcm, creep / shrinkage / ageing switches, ACI 209 overrides);
* appends ``model_as`` and ``es_factor`` columns to the ``tendons`` table.

Untouched rows apply as the identity (every column writes back exactly the
value it read), and a model without the features shows no rows / the
default column values.
"""

from __future__ import annotations

from typing import List

from .modeltables import Col, ObjectTable, Table, _names

num = "number"
G_DEF = "Model Definition > Properties"
_DEF = {"law": "cebfip2010", "rh": 70.0, "h0": 0.15, "cure_days": 7.0,
        "curing": "moist", "creep": True, "shrinkage": True, "aging": True}


def _get(k):
    def g(o, d):
        v = o.get(k)
        if v is None and k in _DEF:
            return _DEF[k]
        if v is None and k == "cement":
            from .timedep import _DEF_CEMENT
            return _DEF_CEMENT.get(o.get("law", "cebfip2010"), "N")
        return v
    return g


def _put(k):
    def p(o, v, d):
        if v is None or v == "":
            o.pop(k, None)
        elif k in o or v != _DEF.get(k, object()):
            o[k] = v
    return p


def _col(k, label, type_=num, q="none", **kw):
    return Col(k, label, type_, q, get=_get(k), put=_put(k), **kw)


def _es_get(o, d):
    return (o.get("losses") or {}).get("es_factor", 0.0)


def _es_put(o, v, d):
    ls = dict(o.get("losses") or {})
    if v in (None, 0, 0.0) and "es_factor" not in ls:
        return
    if v in (None, 0, 0.0):
        ls.pop("es_factor", None)
    else:
        ls["es_factor"] = float(v)
    o["losses"] = ls


def _ma_put(o, v, d):
    if v == "elements":
        o["model_as"] = "elements"
    else:
        o.pop("model_as", None)


def extend(T: List[Table]) -> None:
    from .timedep import LAWS, CURINGS
    mats = _names("materials")
    tbl = ObjectTable(
        "material_time_dependent", "Material Properties - Time Dependent",
        G_DEF, [
            Col("name", "Material", "enum", enum=mats, required=True),
            _col("law", "Law", "enum", enum=LAWS, default="cebfip2010"),
            _col("rh", "Relative Humidity (%)", default=70.0),
            _col("h0", "Notional Size h0", num, "dim", default=0.15),
            _col("cure_days", "Curing Age (days)", default=7.0),
            _col("cement", "Cement", "text", default=""),
            _col("curing", "Curing", "enum", enum=CURINGS, default="moist"),
            _col("fcm", "Mean Strength fcm", num, "stress", optional=True),
            _col("creep", "Creep", "bool", default=True),
            _col("shrinkage", "Shrinkage", "bool", default=True),
            _col("aging", "Ageing", "bool", default=True),
            _col("phi_u", "ACI phi_u", num, optional=True),
            _col("eps_shu", "ACI eps_shu", num, optional=True),
        ], container="material_time_dependent", keycol="name",
        name_in_obj=False, new_obj=lambda d: {"law": "cebfip2010"})
    idx = next((i for i, t in enumerate(T) if t.key == "materials"), None)
    T.insert(idx + 1 if idx is not None else len(T), tbl)
    for t in T:
        if t.key == "tendons":
            t.cols.append(Col("model_as", "Model As", "enum",
                              enum=("loads", "elements"), default="loads",
                              get=lambda o, d: o.get("model_as", "loads"),
                              put=_ma_put))
            t.cols.append(Col("es_factor", "ES Loss Factor", num,
                              default=0.0, get=_es_get, put=_es_put))
