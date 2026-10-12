"""Wave B Interactive Database Editing coverage (one hook line at the end
of :func:`skyframe.core.modeltables._build_catalogue`).

* ``materials``: symmetry enum + ``orthotropic``; columns E2 / G12 / G13 /
  G23 (the ``ortho`` dict - removed again when every value is blank);
* ``shell_sections``: Slab Type (read-only), section-level Load
  Distribution / Span Direction (blank = unset);
* ``shell_objects``: Own Distribution (``distribution_override``);
* ``line_springs``: Property (named line spring property);
* new tables "Spring Properties - Line" / "Spring Properties - Area"
  (kind, k1..k3, nonlinear DOF; multilinear curves are shown as a point
  count and edited in the dialog).

Every column removes its key when the value returns to the default, so an
untouched model -> rows -> apply is the identity.
"""

from __future__ import annotations

from typing import List

from .modeltables import Col, ObjectTable, Table, _opt_names
from .spring_dist import DOFS, KINDS

num = "number"
G_DEF = "Model Definition > Properties"


def _table(T, key) -> Table:
    return next(t for t in T if t.key == key)


def _ortho_get(k):
    return lambda o, d: (o.get("ortho") or {}).get(k)


def _ortho_put(k):
    def put(o, v, d):
        oo = dict(o.get("ortho") or {})
        if v is None:
            oo.pop(k, None)
        else:
            oo[k] = float(v)
        if oo:
            o["ortho"] = oo
        else:
            o.pop("ortho", None)
    return put


def _opt_put(key, cast=None):
    def put(o, v, d):
        if v is None or v == "":
            o.pop(key, None)
        else:
            o[key] = cast(v) if cast else v
    return put


def _k_cols(quantity):
    out = []
    for i, lb in enumerate(("k1", "k2", "k3")):
        def put(o, v, d, i=i):
            k = list(o.get("k") or [0.0, 0.0, 0.0])
            k[i] = float(v or 0.0)
            o["k"] = k
        out.append(Col(lb, lb.upper(), num, quantity, default=0.0,
                       get=lambda o, d, i=i: (o.get("k") or [0.0] * 3)[i],
                       put=put))
    return out


def _spring_table(key, title, container, quantity):
    def refs(d, gone):
        return None
    return ObjectTable(
        key, title, G_DEF, [
            Col("name", "Name", "text", required=True),
            Col("kind", "Kind", "enum", enum=KINDS, default="linear"),
            *_k_cols(quantity),
            Col("nonlinear_dof", "Nonlinear DOF", "enum",
                enum=("",) + DOFS, default="",
                get=lambda o, d: o.get("nonlinear_dof") or "",
                put=_opt_put("nonlinear_dof")),
            Col("n_points", "Curve Points", "int", editable=False,
                get=lambda o, d: sum(len(v) for v in
                                     (o.get("curves") or {}).values())),
        ], container=container, keycol="name", name_in_obj=False,
        new_obj=lambda d: {"kind": "linear", "k": [0.0, 0.0, 0.0]})


def extend_catalogue(T: List[Table]) -> None:
    from .model import MATERIAL_SYMMETRY_EXT
    mt = _table(T, "materials")
    for c in mt.cols:
        if c.key == "symmetry":
            c.enum = MATERIAL_SYMMETRY_EXT
    i = next(k for k, c in enumerate(mt.cols) if c.key == "G") + 1
    mt.cols = mt.cols[:i] + [
        Col(f"ortho_{k}", k, num, "modulus", optional=True,
            get=_ortho_get(k), put=_ortho_put(k))
        for k in ("E2", "G12", "G13", "G23")] + mt.cols[i:]

    ss = _table(T, "shell_sections")
    ss.cols = ss.cols + [
        Col("slab_type", "Slab Type", "text", editable=False,
            get=lambda o, d: (o.get("slab") or {}).get("type", "")
            or ("laminate" if o.get("laminate") else "")),
        Col("sec_distribution", "Load Distribution", "enum",
            enum=("", "two_way", "one_way"), default="",
            get=lambda o, d: o.get("distribution") or "",
            put=_opt_put("distribution")),
        Col("sec_one_way_dir", "Span Direction", "int", optional=True,
            enum=(1, 2), get=lambda o, d: o.get("one_way_dir"),
            put=_opt_put("one_way_dir", int)),
    ]
    so = _table(T, "shell_objects")
    so.cols = so.cols + [
        Col("distribution_override", "Own Distribution", "bool",
            default=False,
            get=lambda o, d: bool(o.get("distribution_override", False)),
            put=lambda o, v, d: (o.__setitem__("distribution_override", True)
                                 if v else o.pop("distribution_override",
                                                 None))),
    ]
    ls = _table(T, "line_springs")
    ls.cols = ls.cols + [
        Col("property", "Property", "enum",
            enum=_opt_names("line_spring_properties"), default="",
            get=lambda o, d: o.get("property") or "",
            put=_opt_put("property")),
    ]
    idx = next(k for k, t in enumerate(T) if t.key == "shell_sections")
    T[idx + 1:idx + 1] = [
        _spring_table("line_spring_properties", "Spring Properties - Line",
                      "line_spring_properties", "line_spring"),
        _spring_table("area_spring_properties", "Spring Properties - Area",
                      "area_spring_properties", "none"),
    ]
