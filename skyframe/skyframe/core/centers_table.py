"""'Centers Of Mass And Rigidity' results table (audit D3).

Columns follow ETABS: per diaphragm the lateral mass, the per-story centre
of mass (XCM, YCM), the CUMULATIVE mass and centre of mass of that
diaphragm and every same-named diaphragm above it (XCCM, YCCM), and the
centre of rigidity (XCR, YCR).  Each column carries a ``tip`` (header
tooltip) with its definition — see CONTRACT "Centers of mass and rigidity
(audit D3)".  Pure post-processing of ``results["story_props"]`` /
``results["diaphragms"]`` and the engine context mass map.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

_CR_TIP = ("Centre of rigidity: the point where a lateral force applied to "
           "this story ONLY (no load on the other stories, which stay free) "
           "produces no rotation of this story - unit Fx, Fy, Mz at the "
           "diaphragm (ETABS definition)")
_CM_TIP = ("Centre of mass of THIS story's lateral mass only (master lump at "
           "the centroid of its gravity-load source + joint / element / "
           "additional masses on the story nodes)")
_CCM_TIP = ("Cumulative centre of mass: this diaphragm and every diaphragm "
            "of the same name above it")


def _c(key: str, label: str, quantity: str, tip: str = "") -> dict:
    d = {"key": key, "label": label, "quantity": quantity}
    if tip:
        d["tip"] = tip
    return d


CMCR_COLS = [
    _c("story", "Story", "text"), _c("diaphragm", "Diaphragm", "text"),
    _c("mass", "Mass", "mass", "Lateral (UX) mass of the diaphragm: master "
       "+ every node of the story"),
    _c("cm_x", "XCM", "length", _CM_TIP), _c("cm_y", "YCM", "length", _CM_TIP),
    _c("cum_mass", "Cumulative Mass", "mass", "Mass of this diaphragm plus "
       "every diaphragm of the same name above it"),
    _c("ccm_x", "XCCM", "length", _CCM_TIP),
    _c("ccm_y", "YCCM", "length", _CCM_TIP),
    _c("cr_x", "XCR", "length", _CR_TIP), _c("cr_y", "YCR", "length", _CR_TIP),
]
_KEYS = [col["key"] for col in CMCR_COLS]


def t_centers(c) -> List[dict]:
    """Rows top -> bottom (``c`` = :class:`skyframe.core.tables._Ctx`)."""
    sp = c.R.get("story_props", {})
    lat: Dict[int, float] = {}
    for t, d, m in c.mass:                     # lateral (UX, else UY) mass
        if d == 1:
            lat[t] = lat.get(t, 0.0) + m
    for t, d, m in c.mass:
        if d == 2 and t not in lat:
            lat[t] = m
    named = (c.R.get("diaphragms") or {}).get("stories") or {}
    rows: List[dict] = []
    for s in reversed(c.stories):
        if s.name in named:                     # named diaphragms
            for dn, e in sorted(named[s.name].items()):
                rows.append({"story": s.name, "diaphragm": dn,
                             "mass": e["mass"],
                             "cm_x": e["cm_x"], "cm_y": e["cm_y"],
                             "cr_x": e.get("cr_x"), "cr_y": e.get("cr_y")})
            continue
        e = sp.get(s.name)
        if e is None:
            continue
        mt = c.masters.get(s.name)
        # the story's whole lateral mass (master + slave nodes: joint
        # masses, element self-mass, additional mass), not the master only
        mass = lat.get(mt, 0.0) + sum(lat.get(t, 0.0) for t in
                                      c.story_nodes.get(s.name, ())
                                      if t != mt)
        rows.append({"story": s.name, "diaphragm": "D1", "mass": mass,
                     "cm_x": e["cm_x"], "cm_y": e["cm_y"],
                     "cr_x": e.get("cr_x"), "cr_y": e.get("cr_y")})
    _cumulative(c.model, rows)
    return [{k: r[k] for k in _KEYS} for r in rows]


def _cumulative(model, rows: List[dict]) -> None:
    """Cumulative mass / XCCM / YCCM per (tower, diaphragm), top down."""
    st: Dict[str, str] = {}
    from .towers import has_towers, story_tower_map
    if has_towers(model):
        st = story_tower_map(model)
    acc: Dict[Tuple[str, str], List[float]] = {}
    for r in rows:
        a = acc.setdefault((st.get(r["story"], ""), r["diaphragm"]),
                           [0.0, 0.0, 0.0])
        m = float(r["mass"] or 0.0)
        a[0] += m
        a[1] += m * r["cm_x"]
        a[2] += m * r["cm_y"]
        r["cum_mass"] = a[0]
        if a[0] > 0.0:
            r["ccm_x"], r["ccm_y"] = a[1] / a[0], a[2] / a[0]
        else:
            r["ccm_x"], r["ccm_y"] = r["cm_x"], r["cm_y"]
