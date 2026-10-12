"""Joint reactions in joint local axes (Wave B; ETABS Joint Reactions with
"Local" coordinate system).

Pure post-processing of a results dict + model.  Only models with
``joint_local_axes`` (core/releases.py) are affected: the "Joint Reactions"
table (core/tables.py) gains the columns ``F1 F2 F3 M1 M2 M3`` (the
reaction resolved on the joint's local 1/2/3 axes, ``f_local = R^T
f_global`` with ``R`` the columns = local axes, see
:func:`skyframe.core.releases.global_to_joint_local`) and ``LocalAxes``
("yes" when the joint has non-identity local axes).  A joint without local
axes reports ``F1..M3 = FX..MZ``.  The table rows are signed single-valued
sources only (static cases and additive combos), for which the rotation is
exact.  Models without joint local axes keep the legacy columns and rows
byte-identically.

``local_reactions(model, results, case)`` returns ``{joint tag: [F1, F2,
F3, M1, M2, M3]}`` for the joints with local axes (used by
``GET/POST /api/results/joint-local-reactions`` and the 3D display).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import releases as _rel

LOCAL_KEYS = ("F1", "F2", "F3", "M1", "M2", "M3")


def active(model) -> bool:
    return bool(getattr(model, "joint_local_axes", None))


def _cols():
    from .tables import _col
    return ([_col("local_axes", "Local Axes", "text")]
            + [_col(k, k, "force" if k[0] == "F" else "moment")
               for k in LOCAL_KEYS])


def decorate(key: str, model, cols: List[dict], rows: List[dict]):
    """Joint Reactions table hook: extra local columns (only with joint
    local axes)."""
    if key != "joint_reactions" or not active(model):
        return cols, rows
    amap = _rel.joint_axes_map(model)
    for r in rows:
        p = (r["x"], r["y"], r["z"])
        g = [r["FX"], r["FY"], r["FZ"], r["MX"], r["MY"], r["MZ"]]
        has = _rel._pkey(p) in amap
        loc = _rel.global_to_joint_local(model, p, g) if has else g
        r["local_axes"] = "yes" if has else ""
        for k, v in zip(LOCAL_KEYS, loc):
            r[k] = v
    return list(cols) + _cols(), rows


def local_reactions(model, results: dict, case: Optional[str] = None
                    ) -> Dict[str, dict]:
    """``{case: {joint tag (str): {"point", "angles", "local": [6],
    "global": [6]}}}`` for joints with non-identity local axes and a
    reaction, over static cases and single-valued combos (or ``case``)."""
    amap = _rel.joint_axes_map(model)
    angles = {_rel._pkey(e["point"]): list(e["angles"])
              for e in getattr(model, "joint_local_axes", None) or []}
    nodes = {str(t): c for t, c in (results.get("nodes") or {}).items()}
    out: Dict[str, dict] = {}
    if not amap:
        return out
    blocks = [("cases", n, cd) for n, cd in
              (results.get("cases") or {}).items()]
    blocks += [("combos", n, cd) for n, cd in
               (results.get("combos") or {}).items() if "min" not in cd]
    for _blk, name, cd in blocks:
        if case is not None and name != case:
            continue
        per: Dict[str, dict] = {}
        for ts, g in (cd.get("reactions") or {}).items():
            p = nodes.get(str(ts))
            if p is None or _rel._pkey(p) not in amap:
                continue
            per[str(ts)] = {
                "point": list(p), "angles": angles.get(_rel._pkey(p)),
                "global": [float(v) for v in g],
                "local": _rel.global_to_joint_local(model, p, g)}
        if per:
            out[name] = per
    return out


__all__ = ["LOCAL_KEYS", "active", "decorate", "local_reactions"]
