"""Interactive Database Editing coverage for the Wave B hinge features.

One hook line in :func:`skyframe.core.modeltables_ext.extend_catalogue`
calls :func:`extend`, which

* appends auto-hinge columns to "Frame Hinge Properties" (``auto_code``,
  ``auto_component``, ``auto_dof``, ``auto_params`` as compact JSON) —
  blank for every non-auto property, so legacy rows are unchanged;
* adds "Frame Hinge Properties - Interaction Surface" (one row per
  (property, angle, point) of a PMM / M2M3 surface);
* adds "Wall Hinges" (one row per pier label, every definition key);
* teaches the backbone table not to write an empty backbone into a
  property that has none (auto / PMM_fiber), so an untouched model ->
  rows -> apply stays the identity.
"""

from __future__ import annotations

import json
from typing import List

from .modeltables import Col, ObjectTable, Table, _names

G_DEF = "Model Definition > Properties"
num = "number"


def _auto_get(key):
    def g(o, d):
        a = o.get("auto") if o.get("type") == "auto" else None
        if not a:
            return ""
        if key == "params":
            return json.dumps(a.get("params") or {}, sort_keys=True)
        return a.get(key) or ""
    return g


def _auto_put(key):
    def p(o, v, d):
        if o.get("type") != "auto":
            if v in ("", None):
                return
            raise ValueError("only auto hinge properties take auto columns")
        a = dict(o.get("auto") or {})
        if key == "params":
            try:
                pv = json.loads(v) if v else {}
            except ValueError:
                raise ValueError("params must be a JSON object") from None
            if not isinstance(pv, dict):
                raise ValueError("params must be a JSON object")
            a["params"] = pv
        else:
            a[key] = v
        o["auto"] = a
    return p


class SurfaceTable(Table):
    """Rows = (property, angle, P, M) of every interacting hinge."""

    editable = True
    can_add = True
    can_delete = True

    def rows(self, d):
        out = []
        for name, p in (d.get("hinge_properties") or {}).items():
            if p.get("type") not in ("PMM", "M2M3"):
                continue
            for c in (p.get("surface") or {}).get("curves") or []:
                for k, (P, M) in enumerate(c.get("points") or []):
                    out.append({"_id": f"{name}|{c['angle']}|{k}",
                                "hinge": name, "angle": c["angle"],
                                "P": P, "M": M})
        return out

    def apply(self, d, rows, errs):
        n0 = len(errs)
        by: dict = {}
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                errs.append({"table": self.key, "row": i, "col": None,
                             "message": "row must be an object"})
                continue
            name = r.get("hinge")
            p = (d.get("hinge_properties") or {}).get(name)
            if p is None or p.get("type") not in ("PMM", "M2M3"):
                errs.append({"table": self.key, "row": i, "col": "hinge",
                             "message": "must name a PMM / M2M3 property"})
                continue
            try:
                ang, P, M = (float(r.get("angle")), float(r.get("P")),
                             float(r.get("M")))
            except (TypeError, ValueError):
                errs.append({"table": self.key, "row": i, "col": None,
                             "message": "angle, P and M must be numbers"})
                continue
            by.setdefault(name, {}).setdefault(ang, []).append([P, M])
        if len(errs) > n0:
            return {}
        changed = []
        for name, p in (d.get("hinge_properties") or {}).items():
            if p.get("type") not in ("PMM", "M2M3"):
                continue
            new = {"curves": [{"angle": a, "points": pts}
                              for a, pts in sorted(by.get(name, {}).items())]}
            old = {"curves": [{"angle": float(c["angle"]),
                               "points": [[float(x), float(y)]
                                          for x, y in c["points"]]}
                              for c in (p.get("surface") or {}).get(
                                  "curves") or []]}
            if json.dumps(new, sort_keys=True) != json.dumps(
                    old, sort_keys=True):
                p["surface"] = new
                changed.append(name)
        return {"changed": changed}


def _stories_text_get(o, d):
    s = o.get("stories")
    return "" if s is None else ", ".join(s)


def _stories_text_put(o, v, d):
    s = (v or "").strip()
    o["stories"] = None if not s else [x.strip() for x in s.split(",")
                                       if x.strip()]


def extend(T: List[Table]) -> None:
    from .wall_hinges import WALL_KEYS
    hp = next((t for t in T if t.key == "hinge_properties"), None)
    if hp is not None:
        hp.cols.extend([
            Col("auto_code", "Auto Code", "text", default="",
                get=_auto_get("code"), put=_auto_put("code")),
            Col("auto_component", "Auto Component", "text", default="",
                get=_auto_get("component"), put=_auto_put("component")),
            Col("auto_dof", "Auto DOF", "text", default="",
                get=_auto_get("dof"), put=_auto_put("dof")),
            Col("auto_params", "Auto Params (JSON)", "text", default="",
                get=_auto_get("params"), put=_auto_put("params")),
        ])
    bb = next((t for t in T if t.key == "hinge_backbones"), None)
    if bb is not None:
        store0 = bb.store

        def store(p, by_tag, _s=store0):
            if not by_tag and "backbone" not in p:
                return                  # auto / PMM_fiber: no backbone
            _s(p, by_tag)
        bb.store = store
    surf = SurfaceTable("hinge_surfaces",
                        "Frame Hinge Properties - Interaction Surface",
                        G_DEF, [
                            Col("hinge", "Hinge Property", "enum",
                                enum=_names("hinge_properties"),
                                required=True),
                            Col("angle", "Angle", num, "angle_deg",
                                required=True),
                            Col("P", "P", num, "force", required=True),
                            Col("M", "M", num, "moment", required=True)])
    walls = ObjectTable(
        "wall_hinges", "Wall Hinges (Fiber P-M3)", G_DEF, [
            Col("pier", "Pier", "text", required=True),
            Col("rho_v", "Vertical Steel Ratio", num,
                default=WALL_KEYS["rho_v"]),
            Col("rho_b", "Boundary Steel Ratio", num,
                default=WALL_KEYS["rho_b"]),
            Col("boundary_ratio", "Boundary Length / lw", num,
                default=WALL_KEYS["boundary_ratio"]),
            Col("fy", "Rebar fy", num, "stress", default=WALL_KEYS["fy"]),
            Col("fc", "Concrete fc", num, "stress", optional=True),
            Col("n_fibers", "Fibers", "int", default=WALL_KEYS["n_fibers"]),
            Col("lp_ratio", "Hinge Length / lw", num,
                default=WALL_KEYS["lp_ratio"]),
            Col("confined", "Confined Boundary", "bool", default=False),
            Col("stories", "Stories", "text", default="",
                get=_stories_text_get, put=_stories_text_put),
        ], container="wall_hinges", keycol="pier", name_in_obj=False,
        new_obj=lambda d: dict(WALL_KEYS))
    idx = next((i for i, t in enumerate(T) if t.key == "hinge_backbones"),
               len(T) - 1)
    T[idx + 1:idx + 1] = [surf, walls]
