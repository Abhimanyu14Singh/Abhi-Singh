"""``cSapModel`` (the root ``SapModel`` object), its registry, the
registry -> :class:`BuildingModel` translation and the analysis call.

See :mod:`skyframe.oapi.sapmodel` for the per-object methods and
:mod:`skyframe.oapi.results` for ``SapModel.Results``.
"""

from __future__ import annotations

import copy
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

from . import sapmodel as _sm
from .results import cAnalysisResults
from .units import EUNITS, PresentUnits

VERSION = "1.0.0"

_LOCAL = {1: 0, 2: 1, 3: 2}            # CSI local dir -> axis index
_GVEC = {"global_x": (1.0, 0.0, 0.0), "global_y": (0.0, 1.0, 0.0),
         "global_z": (0.0, 0.0, 1.0)}


def _blank_state() -> dict:
    return {
        "points": OrderedDict(), "frames": OrderedDict(),
        "areas": OrderedDict(), "materials": OrderedDict(),
        "frame_props": OrderedDict(), "area_props": OrderedDict(),
        "restraints": {}, "springs": {}, "masses": {},
        "patterns": OrderedDict(), "cases": OrderedDict(),
        "combos": OrderedDict(), "point_loads": OrderedDict(),
        "frame_loads": OrderedDict(), "area_loads": OrderedDict(),
        "stories": [], "base_elev": 0.0, "diaphragms": OrderedDict(),
        "point_diaph": {}, "area_diaph": {}, "groups": OrderedDict(),
        "not_run": set(), "active_dof": [True] * 6, "num_modes": 12,
        "counters": {"points": 0, "frames": 0, "areas": 0},
        "filename": "", "name": "OAPI model",
    }


class cSapModel:
    """The ``SapModel`` root object."""

    def __init__(self, units: int = 6):
        self._units = PresentUnits(units)
        self._db_units = units
        self._st = _blank_state()
        self._results = None           # AnalysisResults of the last run
        self._model = None             # BuildingModel of the last run
        self._locked = False
        self.File = _sm.cFile(self)
        self.PropMaterial = _sm.cPropMaterial(self)
        self.PropFrame = _sm.cPropFrame(self)
        self.PropArea = _sm.cPropArea(self)
        self.PointObj = _sm.cPointObj(self)
        self.FrameObj = _sm.cFrameObj(self)
        self.AreaObj = _sm.cAreaObj(self)
        self.Story = _sm.cStory(self)
        self.Diaphragm = _sm.cDiaphragm(self)
        self.GroupDef = _sm.cGroupDef(self)
        self.LoadPatterns = _sm.cLoadPatterns(self)
        self.LoadCases = _sm.cLoadCases(self)
        self.RespCombo = _sm.cRespCombo(self)
        self.Analyze = _sm.cAnalyze(self)
        self.Results = cAnalysisResults(self)
        self.View = _sm.cView(self)
        self.SelectObj = _sm.cSelectObj(self)

    # ------------------------------------------------------------ top level
    def InitializeNewModel(self, Units=3):
        if int(Units) not in EUNITS:
            return 1
        self._db_units = int(Units)
        self._units = PresentUnits(int(Units))
        self._reset(keep_units=True)
        return 0

    def SetPresentUnits(self, Units):
        if int(Units) not in EUNITS:
            return 1
        self._units = PresentUnits(int(Units))
        return 0

    def GetPresentUnits(self):
        return self._units.code

    def GetDatabaseUnits(self):
        return self._db_units

    def SetModelIsLocked(self, Lock=True):
        self._locked = bool(Lock)
        if not Lock:
            self._results = None
        return 0

    def GetModelIsLocked(self):
        return self._locked

    def GetModelFilename(self, IncludePath=True):
        import os
        fn = self._st.get("filename") or ""
        return fn if IncludePath else os.path.basename(fn)

    def GetVersion(self, Version="", MyVersionNumber=0.0):
        return [f"SkyFrame OAPI facade {VERSION}", 1.0, 0]

    # ------------------------------------------------------------ SkyFrame
    def to_skyframe(self):
        """The :class:`BuildingModel` the registry describes (what
        ``Analyze.RunAnalysis`` solves; post its ``to_dict()`` to ``POST
        /api/model`` to get identical REST results)."""
        return _build_model(self._st)

    @property
    def skyframe_results(self):
        """The :class:`AnalysisResults` of the last ``RunAnalysis``."""
        return self._results

    # ------------------------------------------------------------ internals
    def _reset(self, keep_units: bool = True) -> None:
        self._st = _blank_state()
        self._st["cases"]["MODAL"] = {"type": "modal", "modes": 12}
        self._results = None
        self._model = None
        self._locked = False
        self.Results.Setup._reset()

    def _invalidate(self) -> None:
        self._results = None
        self._model = None
        self._locked = False

    def _next(self, kind: str) -> str:
        c = self._st["counters"]
        reg = self._st[kind]
        while True:
            c[kind] += 1
            nm = str(c[kind])
            if nm not in reg:
                return nm

    def _point(self, p, user: str = "", merge: bool = True) -> Optional[str]:
        pts = self._st["points"]
        if merge:
            for nm, q in pts.items():
                if math.dist(q, p) < _sm._TOL:
                    return nm
        if user and user in pts:
            return None
        nm = user or self._next("points")
        pts[nm] = tuple(float(c) for c in p)
        self._invalidate()
        return nm

    def _targets(self, kind: str, name, item_type) -> Optional[List[str]]:
        """ItemType 0 object, 1 group, 2 selection (= nothing selected)."""
        it = int(item_type or 0)
        if it == 0:
            return [name] if name in self._st[kind] else None
        if it == 1:
            g = self._st["groups"].get(name)
            return None if g is None else list(g[kind])
        return []

    def _group_assign(self, kind, name, group, remove, item_type) -> int:
        names = self._targets(kind, name, item_type)
        g = self._st["groups"].get(group)
        if names is None or g is None:
            return 1
        for n in names:
            if remove:
                if n in g[kind]:
                    g[kind].remove(n)
            elif n not in g[kind]:
                g[kind].append(n)
        self._invalidate()
        return 0

    def _frame_length(self, name: str) -> float:
        f = self._st["frames"][name]
        return math.dist(self._st["points"][f["i"]],
                         self._st["points"][f["j"]])

    def _frame_story(self, name: str) -> str:
        f = self._st["frames"][name]
        z = max(self._st["points"][f["i"]][2], self._st["points"][f["j"]][2])
        return _story_at(self._st, z)

    def _run(self) -> int:
        from skyframe.engine.opensees_engine import OpenSeesEngine
        try:
            model = self.to_skyframe()
            self._results = OpenSeesEngine(model).run()
        except Exception:                          # noqa: BLE001
            self._results = None
            return 1
        self._model = model
        self._locked = True
        return 0

    def _registry_dict(self) -> dict:
        """JSON-safe copy of the registry (stored by File.Save)."""
        st = self._st
        return {
            "points": {k: list(v) for k, v in st["points"].items()},
            "frames": {k: dict(v) for k, v in st["frames"].items()},
            "areas": {k: dict(v) for k, v in st["areas"].items()},
            "area_kinds": {k: v["kind"] for k, v in st["area_props"].items()},
            "restraints": st["restraints"], "springs": st["springs"],
            "masses": st["masses"],
            "patterns": {k: dict(v) for k, v in st["patterns"].items()},
            "cases": {k: dict(v) for k, v in st["cases"].items()},
            "combos": {k: dict(v) for k, v in st["combos"].items()},
            "point_loads": [[k[0], k[1], v] for k, v in
                            st["point_loads"].items()],
            "frame_loads": [[k[0], k[1], v] for k, v in
                            st["frame_loads"].items()],
            "area_loads": [[k[0], k[1], v] for k, v in
                           st["area_loads"].items()],
            "stories": st["stories"], "base_elev": st["base_elev"],
            "diaphragms": dict(st["diaphragms"]),
            "point_diaph": st["point_diaph"], "area_diaph": st["area_diaph"],
            "groups": {k: dict(v) for k, v in st["groups"].items()},
            "not_run": sorted(st["not_run"]),
            "active_dof": st["active_dof"],
            "counters": st["counters"], "name": st["name"],
        }

    def _load(self, d: dict) -> None:
        """Restore from a :meth:`File.Save` file (registry + properties)."""
        from skyframe.core.model import BuildingModel
        reg = d.get("_oapi")
        model = BuildingModel.from_dict({k: v for k, v in d.items()
                                         if k != "_oapi"})
        if reg is None:
            reg = _registry_from_model(model)
        st = _blank_state()
        st["materials"].update(model.materials)
        st["frame_props"].update(model.sections)
        kinds = reg.get("area_kinds", {})
        for n, ss in model.shell_sections.items():
            st["area_props"][n] = {"section": ss, "kind": kinds.get(n, "")}
        st["points"].update({k: tuple(v) for k, v in reg["points"].items()})
        st["frames"].update(reg["frames"])
        st["areas"].update(reg["areas"])
        for key in ("restraints", "springs", "masses", "point_diaph",
                    "area_diaph"):
            st[key] = dict(reg.get(key, {}))
        st["patterns"].update(reg["patterns"])
        st["cases"].update(reg["cases"])
        st["combos"].update({k: {"type": v["type"],
                                 "items": [tuple(x) for x in v["items"]]}
                             for k, v in reg["combos"].items()})
        for key in ("point_loads", "frame_loads", "area_loads"):
            for a, b, v in reg.get(key, []):
                st[key][(a, b)] = v
        st["stories"] = [list(r) for r in reg.get("stories", [])]
        st["base_elev"] = reg.get("base_elev", 0.0)
        st["diaphragms"].update(reg.get("diaphragms", {}))
        st["groups"].update(reg.get("groups", {}))
        st["not_run"] = set(reg.get("not_run", []))
        st["active_dof"] = list(reg.get("active_dof", [True] * 6))
        st["counters"].update(reg.get("counters", {}))
        st["name"] = reg.get("name", model.name)
        self._st = st
        self._invalidate()


# --------------------------------------------------------------------------- #
# registry -> BuildingModel
# --------------------------------------------------------------------------- #
def _story_at(st: dict, z: float) -> str:
    for nm, elev, _h in st["stories"]:
        if abs(elev - z) < 1e-6:
            return nm
    above = [r for r in st["stories"] if r[1] > z]
    if above and z > st.get("base_elev", 0.0) + 1e-6:
        return min(above, key=lambda r: r[1])[0]
    return ""


def _frame_kind(pi, pj) -> str:
    dx, dy, dz = (pj[k] - pi[k] for k in range(3))
    if math.hypot(dx, dy) < 1e-6:
        return "column"
    if abs(dz) < 1e-6:
        return "beam"
    return "brace"


def _build_model(st: dict):
    from skyframe.core import frame_axes as _fa
    from skyframe.core import releases as _rel
    from skyframe.core.model import (AreaLoad, BuildingModel, LoadCombo,
                                     LoadPattern, NodalLoad, NodalMass,
                                     PointSupport, SpringSupport, Story)
    m = BuildingModel(name=st.get("name") or "OAPI model")
    m.frame_axis_convention = _fa.ETABS
    m.rigid_diaphragms = False
    m.diaphragm = "none"
    m.mass_source_mode = "element_self_mass"
    for nm, mat in st["materials"].items():
        m.add_material(copy.deepcopy(mat))
    for nm, sec in st["frame_props"].items():
        m.add_section(copy.deepcopy(sec))
    for nm, p in st["area_props"].items():
        m.add_shell_section(copy.deepcopy(p["section"]))
    if st["stories"]:
        m.stories = [Story(nm, h, z) for nm, z, h in st["stories"]]
    pts = st["points"]

    for nm, f in st["frames"].items():
        pi, pj = pts[f["i"]], pts[f["j"]]
        mem = m.add_member(_frame_kind(pi, pj), f["prop"], pi, pj,
                           story=_story_at(st, max(pi[2], pj[2])),
                           uid=nm, angle=f.get("angle", 0.0))
        if f.get("rel"):
            _rel.set_effective(mem, {e: dict(v) for e, v in
                                     f["rel"].items() if v})
    for nm, a in st["areas"].items():
        corners = [pts[p] for p in a["pts"]]
        prop = st["area_props"][a["prop"]]
        ss = prop["section"]
        zs = [c[2] for c in corners]
        vertical = _is_vertical(corners)
        kind = prop["kind"] or ("wall" if vertical else "slab")
        behavior = "membrane" if (kind == "slab" and
                                  ss.shell_type == "membrane") else "shell"
        if len(corners) == 4:
            m.add_shell(kind, behavior, a["prop"], corners,
                        mesh_size=a.get("mesh", 1.0),
                        story=_story_at(st, max(zs)), uid=nm)
        else:
            from skyframe.core.model import ShellRegion
            r = ShellRegion(nm, kind, behavior, a["prop"],
                            [tuple(c) for c in corners],
                            mesh_size=a.get("mesh", 1.0),
                            story=_story_at(st, max(zs)))
            m.shells.append(r)

    used = {p for f in st["frames"].values() for p in (f["i"], f["j"])} | \
        {p for a in st["areas"].values() for p in a["pts"]}
    for p, v in st["restraints"].items():
        if p in used and any(v):
            m.supports.append(PointSupport(pts[p], tuple(int(bool(x))
                                                         for x in v)))
    for p, k in st["springs"].items():
        if p in used and any(k):
            m.spring_supports.append(SpringSupport(pts[p], list(k)))
    for p, ms in st["masses"].items():
        if p in used and any(ms):
            m.nodal_masses.append(NodalMass(pts[p], *ms))

    # diaphragms -> per-story rigid diaphragm (semi-rigid = slab membrane)
    rigid_stories = set()
    for p, dn in st["point_diaph"].items():
        if p in used and not st["diaphragms"].get(dn, True):
            s = _story_at(st, pts[p][2])
            if s and any(abs(r[1] - pts[p][2]) < 1e-6
                         for r in st["stories"]):
                rigid_stories.add(s)
    for a, dn in st["area_diaph"].items():
        if a in st["areas"] and not st["diaphragms"].get(dn, True):
            z = max(pts[p][2] for p in st["areas"][a]["pts"])
            s = _story_at(st, z)
            if s:
                rigid_stories.add(s)
    if rigid_stories:
        m.rigid_diaphragms = True
        m.story_diaphragm = {s.name: ("rigid" if s.name in rigid_stories
                                      else "none") for s in m.stories}

    # loads
    for nm, p in st["patterns"].items():
        pat = LoadPattern(nm, _sm.PATTERN_KINDS.get(p["type"], "other"))
        pat.self_weight_factor = float(p["sw"])
        m.patterns[nm] = pat
    for (pt, pat), v in st["point_loads"].items():
        if pt in used and pat in m.patterns and any(v):
            m.patterns[pat].nodal_loads.append(NodalLoad(pts[pt], *v))
    for (fr, pat), lst in st["frame_loads"].items():
        if fr not in st["frames"] or pat not in m.patterns:
            continue
        mem = next(x for x in m.members if x.uid == fr)
        for ld in lst:
            m.patterns[pat].member_loads.extend(_member_loads(mem, ld))
    for (ar, pat), lst in st["area_loads"].items():
        if ar not in st["areas"] or pat not in m.patterns:
            continue
        for ld in lst:
            m.patterns[pat].area_loads.append(AreaLoad(
                ar, ld["q"], direction=_sm._AREA_DIR[ld["dir"]],
                projected=ld["dir"] == 11))

    # cases / combos
    modal = [c for c in st["cases"].values() if c["type"] == "modal"]
    m.num_modes = int(modal[0]["modes"]) if modal else 0
    for nm, c in st["cases"].items():
        if c["type"] != "static":
            continue
        pats: Dict[str, float] = {}
        for _t, ln, sf in c["loads"]:
            if ln in m.patterns:
                pats[ln] = pats.get(ln, 0.0) + float(sf)
        m.add_case(nm, pats)
    for nm, c in st["combos"].items():
        items = {cn: sf for _t, cn, sf in c["items"]
                 if cn in m.cases or cn in st["combos"]}
        m.combos[nm] = LoadCombo(nm, items, _sm.COMBO_TYPES[c["type"]])
    from skyframe.core.model import DOF_LABELS
    m.active_dof = [d for d, on in zip(DOF_LABELS, st["active_dof"]) if on]
    not_run = [n for n in st["not_run"] if n in st["cases"]]
    if any(st["cases"][n]["type"] == "modal" for n in not_run):
        from skyframe.core.model import MODAL_CASE
        not_run = [n for n in not_run if st["cases"][n]["type"] != "modal"]
        not_run.append(MODAL_CASE)
    m.cases_not_run = sorted(not_run)
    groups = st["groups"]
    for g, v in groups.items():
        m.add_group(g, members=[x for x in v["frames"]
                                if x in st["frames"]],
                    shells=[x for x in v["areas"] if x in st["areas"]],
                    points=[list(pts[x]) for x in v["points"]
                            if x in used])
    _fa.sync_members(m)
    m.validate()
    return m


def _is_vertical(corners) -> bool:
    c = corners
    a = [c[1][k] - c[0][k] for k in range(3)]
    b = [c[-1][k] - c[0][k] for k in range(3)]
    n = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
         a[0] * b[1] - a[1] * b[0])
    ln = math.sqrt(sum(v * v for v in n)) or 1.0
    return abs(n[2] / ln) < 1e-6


def _member_loads(mem, ld) -> list:
    """Registry frame load -> SkyFrame MemberLoad(s).  Local 1 / 3 loads
    (no native SkyFrame direction) are resolved into exact global
    components along the member's local axis."""
    from skyframe.core import frame_axes as _fa
    from skyframe.core.model import MemberLoad
    d = ld["dir"]
    if ld["kind"] == "moment":
        dirn = {1: "local_x", 2: "local_y", 3: "local_z", 4: "global_x",
                5: "global_y", 6: "global_z"}[d]
        return [MemberLoad(mem.uid, kind="moment", w=ld["w1"], a=ld["a"],
                           direction=dirn)]
    proj = d in (7, 8, 9, 11)
    if d == 2:
        comps = [("local_y", 1.0)]
    elif d in (1, 3):
        axes = _fa.member_local_axes(mem)
        vec = axes[_LOCAL[d]]
        comps = [(k, c) for k, c in zip(("global_x", "global_y",
                                         "global_z"), vec)
                 if abs(c) > 1e-15]
    else:
        comps = [(_sm._GLOBAL_DIR[d], 1.0)]
    out = []
    for dirn, f in comps:
        if ld["kind"] == "point":
            out.append(MemberLoad(mem.uid, kind="point", w=ld["w1"] * f,
                                  a=ld["a"], direction=dirn))
            continue
        w1, w2 = ld["w1"] * f, ld["w2"] * f
        kind = "udl" if w1 == w2 else "trapezoid"
        out.append(MemberLoad(mem.uid, kind=kind, w=w1,
                              w2=0.0 if kind == "udl" else w2,
                              a=ld["a"], b=ld["b"], direction=dirn,
                              projected=proj))
    return out


def _registry_from_model(model) -> dict:
    """Registry of a plain SkyFrame model (File.OpenFile on a model saved
    by the web UI): frames / areas / supports / loads / cases / combos."""
    pts: Dict[Tuple[float, ...], str] = OrderedDict()

    def pid(p):
        key = tuple(round(float(c), 9) for c in p)
        if key not in pts:
            pts[key] = str(len(pts) + 1)
        return pts[key]

    frames = {m.uid: {"i": pid(m.pi), "j": pid(m.pj), "prop": m.section,
                      "angle": m.angle, "rel": None} for m in model.members}
    areas = {r.uid: {"pts": [pid(c) for c in r.corners], "prop": r.section,
                     "mesh": r.mesh_size} for r in model.shells}
    restraints = {pid(s.point): [bool(v) for v in s.restraints]
                  for s in model.supports}
    _code = {v: k for k, v in _sm.PATTERN_KINDS.items()}
    patterns = {n: {"type": _sm._PATTERN_CODE.get(p.kind, 8),
                    "sw": p.self_weight_factor}
                for n, p in model.patterns.items()}
    cases = {"MODAL": {"type": "modal", "modes": model.num_modes}}
    for n, c in model.cases.items():
        cases[n] = {"type": "static", "loads": [("Load", p, f) for p, f in
                                                c.patterns.items()]}
    combos = {n: {"type": {v: k for k, v in _sm.COMBO_TYPES.items()}.get(
        c.combo_type, 0), "items": [(1 if cn in model.combos else 0, cn, f)
                                    for cn, f in c.cases.items()]}
        for n, c in model.combos.items()}
    point_loads = []
    for n, p in model.patterns.items():
        for nl in p.nodal_loads:
            point_loads.append([pid(nl.point), n,
                                [nl.fx, nl.fy, nl.fz, nl.mx, nl.my, nl.mz]])
    return {"points": {v: list(k) for k, v in pts.items()},
            "frames": frames, "areas": areas, "restraints": restraints,
            "patterns": patterns, "cases": cases, "combos": combos,
            "point_loads": point_loads,
            "stories": [[s.name, s.elevation, s.height]
                        for s in model.stories],
            "base_elev": (model.stories[0].elevation - model.stories[0].height
                          if model.stories else 0.0),
            "name": model.name}
