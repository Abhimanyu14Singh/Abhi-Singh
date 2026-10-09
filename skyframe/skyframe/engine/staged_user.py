"""User-defined (ETABS-style) staged construction.

``StagedCase.stages`` given as a LIST of stages::

    [{"name": "S1", "duration_days": 7,
      "operations": [{"op": "add", "group": "COLS1", "age_days": 28},
                     {"op": "load", "group": "COLS1", "pattern": "DEAD",
                      "scale": 1.0},
                     {"op": "remove", "group": "PROPS"}]}, ...]

is analysed by :func:`run_user_staged` with the same REBUILD-AND-ACCUMULATE
method as the ``per_story`` mode (linear, geometry updating ignored):

* the ACTIVE structure starts empty; ``add`` activates a group's members /
  shells / links (and its points: an explicit support or point spring at a
  group point is active only while that point is active).  Objects are
  added UNSTRESSED — at stage s a fresh model of the active objects is
  solved under ONLY the stage's load increments, so a newly added element
  sees no earlier load and no earlier deformation (ETABS "added
  unstressed"); node displacements accumulate by coordinate;
* ``remove`` deactivates a group: the ACCUMULATED global end forces each
  removed object was exerting (frame members: the 12 member end forces at
  pi/pj rotated to global; links / shells: the OpenSees element resisting
  forces at their nodes) are applied as nodal loads on the remaining
  structure at every joint that is still present (the force the removed
  object carried is redistributed); its accumulated forces are discarded
  and supports that lose their node drop their reaction;
* ``load`` applies a pattern x scale to the group's ACTIVE objects only
  (member / area / thermal loads on group objects, joint loads at the
  group's explicit ``points`` only — and only once a node exists there —
  self-weight
  expanded into explicit member/area loads of the group's objects; story
  forces and ground displacements are skipped with a warning);
* all operations of a stage are applied together in ONE linear solve on
  the stage's active structure (load increments + release forces).

``include_live`` is applied at the end on the final active structure (one
unstaged increment, E28).  ``StagedResults.case`` is the final state as a
normal CaseResults on the FULL model's node tags (objects inactive at the
end report zero forces), ``oneshot`` is the full model under the same
total loads at once, and ``stages`` lists the cumulative state at the end
of every stage.

Time-dependent effects (``time_dependent``, the v0.25 AAEM model) use the
STAGE TIMELINE: stage s starts at ``T_s = sum(duration_days of earlier
stages)``; an object added in stage a with ``age_days`` (default 28) was
cast at ``T_a - age_days``; stage-s loads (incl. release forces) act from
``T_s`` to ``t_eval`` (default: the END of the last stage, i.e. the sum of
all durations; infinity when that sum is 0) with
``E_adj = E(t0)/(1 + 0.8*phi(t_eval - T_s))``, ``E(t0)`` the optional ACI
209 aging growth at the object's age at T_s; one final shrinkage increment
on the final active structure uses each member's age at t_eval.
``days_per_story`` is not used.  See CONTRACT "Groups and user-defined
staged construction".
"""

from __future__ import annotations

import copy
import math
import warnings
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import numpy as np
import openseespy.opensees as ops

from skyframe.core.groups import (GROUP_KINDS, normalize_group,
                                  normalize_stages)
from skyframe.core.model import (AreaLoad, BuildingModel, LoadCase,
                                 LoadPattern, MemberLoad, NodalLoad,
                                 ThermalLoad)
from skyframe.engine.opensees_engine import (DOF_LABELS, TD_CHI, CaseResults,
                                             OpenSeesEngine, StagedResults,
                                             _TOL, _local_axes, _pkey,
                                             aci209_creep,
                                             aci209_modulus_growth,
                                             aci209_shrinkage)

_ZERO6 = np.zeros(6)
_FORCE_KEYS = ("FX", "FY", "FZ", "MX", "MY", "MZ")


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _obj_points(model: BuildingModel, kind: str, uid: str,
                lookup: dict) -> List[tuple]:
    obj = lookup[kind].get(uid)
    if obj is None:
        return []
    if kind == "shells":
        return [tuple(c) for c in obj.corners]
    return [tuple(obj.pi), tuple(obj.pj)]


def _active_keys(model, active: Dict[str, set], lookup: dict) -> set:
    keys = set()
    for kind in GROUP_KINDS:
        for uid in active[kind]:
            for p in _obj_points(model, kind, uid, lookup):
                keys.add(_pkey(p))
    return keys


def _submodel(model: BuildingModel, active: Dict[str, set], keys: set,
              supports, springs, line_springs, label: str
              ) -> BuildingModel:
    """Fresh model holding only the active objects (+ given supports)."""
    sub = BuildingModel(name=f"{model.name} [{label}]")
    sub.materials = dict(model.materials)
    sub.sections = dict(model.sections)
    sub.shell_sections = dict(model.shell_sections)
    sub.grid = model.grid
    sub.stories = [s for s in model.stories
                   if any(abs(k[2] - s.elevation) < _TOL for k in keys)]
    names = {s.name for s in sub.stories}
    sub.members = [m for m in model.members if m.uid in active["members"]]
    sub.shells = [r for r in model.shells if r.uid in active["shells"]]
    sub.links = [lk for lk in model.links if lk.uid in active["links"]]
    sub.base_fixity = model.base_fixity
    sub.supports = list(supports)
    sub.spring_supports = list(springs)
    sub.line_springs = list(line_springs)
    sub.thermal_alpha = model.thermal_alpha
    sub.rigid_diaphragms = model.rigid_diaphragms
    sub.diaphragm = model.diaphragm
    sub.story_diaphragm = {s: v for s, v in model.story_diaphragm.items()
                           if s in names}
    sub.panel_zones = getattr(model, "panel_zones", "none")
    sub.active_dof = list(getattr(model, "active_dof", DOF_LABELS))
    sub.num_modes = 0
    return sub


def _apply_td_scales(sub: BuildingModel, scale_of: Dict[Tuple[str, str],
                                                         float],
                     mats: set) -> None:
    """Retarget concrete members/shells of ``sub`` to cloned sections with
    E (members, via a cloned material) / ``mod`` (shells) scaled."""
    mat_cache: Dict[Tuple[str, float], str] = {}
    sec_cache: Dict[Tuple[str, float], str] = {}
    n = [0]

    def tag() -> int:
        n[0] += 1
        return n[0]

    new_members = []
    for m in sub.members:
        s = scale_of.get(("members", m.uid), 1.0)
        sec = sub.sections.get(m.section)
        if sec is None or s == 1.0 or sec.material not in mats:
            new_members.append(m)
            continue
        key = (m.section, s)
        if key not in sec_cache:
            mkey = (sec.material, s)
            if mkey not in mat_cache:
                mat = copy.copy(sub.materials[sec.material])
                mat.name = f"__ustd__{sec.material}#{tag()}"
                mat.E = sub.materials[sec.material].E * s
                sub.materials[mat.name] = mat
                mat_cache[mkey] = mat.name
            sclone = copy.copy(sec)
            sclone.name = f"__ustd__{m.section}#{tag()}"
            sclone.material = mat_cache[mkey]
            sub.sections[sclone.name] = sclone
            sec_cache[key] = sclone.name
        mc = copy.copy(m)
        mc.section = sec_cache[key]
        new_members.append(mc)
    sub.members = new_members

    ssec_cache: Dict[Tuple[str, float], str] = {}
    new_shells = []
    for r in sub.shells:
        s = scale_of.get(("shells", r.uid), 1.0)
        ssec = sub.shell_sections.get(r.section)
        if ssec is None or s == 1.0 or ssec.material not in mats:
            new_shells.append(r)
            continue
        key = (r.section, s)
        if key not in ssec_cache:
            sclone = copy.copy(ssec)
            sclone.name = f"__ustd__{r.section}#{tag()}"
            sclone.mod = ssec.mod * s
            sub.shell_sections[sclone.name] = sclone
            ssec_cache[key] = sclone.name
        rc = copy.copy(r)
        rc.section = ssec_cache[key]
        new_shells.append(rc)
    sub.shells = new_shells


# --------------------------------------------------------------------------- #
# load expansion
# --------------------------------------------------------------------------- #
def _expand_load(model: BuildingModel, pat: LoadPattern, scale: float,
                 g: dict, objs: Dict[str, set], lookup: dict,
                 out: LoadPattern, warn: List[str]) -> None:
    """Append ``pat`` x ``scale`` restricted to objects ``objs`` (the
    group's objects that receive load) into ``out``."""
    mem, sh = objs["members"], objs["shells"]
    for ml in pat.all_member_loads():
        if ml.member_uid in mem:
            out.member_loads.append(replace(ml, w=ml.w * scale,
                                            w2=ml.w2 * scale))
    for al in pat.area_loads:
        if al.region_uid in sh:
            out.area_loads.append(replace(al, q=al.q * scale))
    for tl in getattr(pat, "thermal_loads", ()):
        if tl.member_uid in mem:
            out.thermal_loads.append(replace(tl, dT=tl.dT * scale))
    # joint loads: only at the group's explicit points (a joint shared by
    # two groups' objects would otherwise be loaded twice)
    pts = {_pkey(tuple(p)) for p in g["points"]}
    for nl in pat.nodal_loads:
        if _pkey(tuple(nl.point)) in pts:
            out.nodal_loads.append(NodalLoad(
                tuple(nl.point), nl.fx * scale, nl.fy * scale,
                nl.fz * scale, getattr(nl, "mx", 0.0) * scale,
                getattr(nl, "my", 0.0) * scale,
                getattr(nl, "mz", 0.0) * scale))
    if pat.story_forces:
        warn.append(f"pattern {pat.name!r}: story forces are not applied "
                    "by staged 'load' operations")
    if getattr(pat, "ground_displacements", None):
        warn.append(f"pattern {pat.name!r}: ground displacements are not "
                    "applied by staged 'load' operations")
    swf = getattr(pat, "self_weight_factor", 0.0)
    if swf:
        for m in model.members:
            if m.uid not in mem:
                continue
            sec = model.sections.get(m.section)
            mat = model.materials.get(sec.material) if sec else None
            if sec is None or mat is None:
                continue
            w_sw = swf * sec.A * mat.unit_weight * sec.mod_weight
            if w_sw:
                out.member_loads.append(MemberLoad(
                    m.uid, kind="udl", w=-w_sw * scale, w2=0.0, a=0.0,
                    b=1.0, direction="global_z"))
        for r in model.shells:
            if r.uid not in sh:
                continue
            ssec = model.shell_sections.get(r.section)
            mat = model.materials.get(ssec.material) if ssec else None
            if ssec is None or mat is None:
                continue
            q_sw = swf * ssec.total_thickness * mat.unit_weight * ssec.weight
            if q_sw:
                out.area_loads.append(AreaLoad(r.uid, q_sw * scale))


def _pattern_empty(p: LoadPattern) -> bool:
    return not (p.member_loads or p.member_udls or p.area_loads
                or p.nodal_loads or p.thermal_loads or p.story_forces)


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def run_user_staged(engine: OpenSeesEngine, name: str) -> StagedResults:
    """Run one user-defined staged case (see module docstring)."""
    model = engine.model
    sc = model.staged_cases[name]
    model._validate_staged_case(sc)
    stages = normalize_stages(sc.stages)
    groups = {n: normalize_group(g) for n, g in model.groups.items()}
    lookup = {"members": {m.uid: m for m in model.members},
              "shells": {r.uid: r for r in model.shells},
              "links": {lk.uid: lk for lk in model.links}}
    warn: List[str] = []

    # ---- timeline
    t_start, t = [], 0.0
    for st in stages:
        t_start.append(t)
        t += st["duration_days"]
    t_total = t
    td = sc.time_dependent
    tdn = None
    if td:
        mats = td.get("materials")
        t_eval = td.get("t_eval")
        if t_eval is None:
            t_eval = t_total if t_total > 0.0 else math.inf
        tdn = {"phi_inf": float(td.get("creep_coeff", 2.0)),
               "eps_inf": float(td.get("shrinkage", 300e-6)),
               "aging": bool(td.get("aging", False)),
               "t_eval": float(t_eval),
               "materials": (set(model.materials) if mats is None
                             else {str(m) for m in mats})}

    # points referenced by add/remove operations gate their supports
    staged_pts = set()
    for st in stages:
        for op in st["operations"]:
            if op["op"] in ("add", "remove"):
                staged_pts.update(_pkey(tuple(p))
                                  for p in groups[op["group"]]["points"])

    # ---- full model (node tags of the final CaseResults + one-shot)
    every = {k: set(lookup[k]) for k in GROUP_KINDS}
    full_keys = _active_keys(model, every, lookup)
    full = _submodel(model, every, full_keys, model.supports,
                     model.spring_supports, model.line_springs, "full")
    full.stories = list(model.stories)
    full.story_diaphragm = dict(model.story_diaphragm)
    oneshot_pat = LoadPattern("__oneshot__", "other")
    for st in stages:
        for op in st["operations"]:
            if op["op"] == "load":
                g = groups[op["group"]]
                _expand_load(model, model.patterns[op["pattern"]],
                             float(op["scale"]), g,
                             {k: set(g[k]) for k in GROUP_KINDS}, lookup,
                             oneshot_pat, [])
    full.patterns = dict(model.patterns)
    full.patterns["__oneshot__"] = oneshot_pat
    facs = {"__oneshot__": 1.0}
    for p, f in sc.include_live.items():
        facs[p] = facs.get(p, 0.0) + f
    full.cases = {"__oneshot__": LoadCase("__oneshot__", facs)}
    eng_full = OpenSeesEngine(full)
    oneshot = eng_full.run_static("__oneshot__")
    full_asm = eng_full._asm
    full_zmin = min((c[2] for c in full_asm.struct_coords.values()),
                    default=0.0)

    # ---- accumulators
    acc = {"disp": {}, "master": {}, "reac": {}, "mf": {}, "st": {},
           "story": {}, "ele": {}}
    active = {k: set() for k in GROUP_KINDS}
    active_pts: set = set()
    t_cast: Dict[Tuple[str, str], float] = {}

    def supports_for(keys: set, label: str):
        def ok(p):
            k = _pkey(tuple(p))
            return k in keys and (k not in staged_pts or k in active_pts)
        sups = [s for s in model.supports if ok(s.point)]
        springs = [s for s in model.spring_supports if ok(s.point)]
        lsp = [ls for ls in model.line_springs
               if ok(ls.p1) and ok(ls.p2)]
        if model.supports and not sups and not springs:
            raise ValueError(f"Staged case {name!r}: {label}: no active "
                             "support")
        if not model.supports:
            zmin = min(k[2] for k in keys)
            if zmin > full_zmin + _TOL and not springs:
                raise ValueError(
                    f"Staged case {name!r}: {label}: the active structure "
                    f"does not reach the base level z={full_zmin:g}")
        return sups, springs, lsp

    def solve(sub: BuildingModel, pat: LoadPattern, extra: Optional[dict]
              = None):
        sub.patterns = dict(model.patterns)
        sub.patterns[pat.name] = pat
        facs = {pat.name: 1.0}
        facs.update(extra or {})
        sub.cases = {"__ustage__": LoadCase("__ustage__", facs)}
        eng = OpenSeesEngine(sub)
        res = eng.run_static("__ustage__")
        asm = eng._asm
        # accumulate the standard fields (keyed by coordinates)
        for tg, c in asm.struct_coords.items():
            k = _pkey(c)
            acc["disp"][k] = acc["disp"].get(k, _ZERO6) + np.asarray(
                res.node_disp[tg])
        for s_name, mt in asm.masters.items():
            acc["master"][s_name] = (acc["master"].get(s_name, _ZERO6)
                                     + np.asarray(res.node_disp[mt]))
        for tg in asm.support_tags:
            k = _pkey(asm.node_coords[tg])
            acc["reac"][k] = acc["reac"].get(k, _ZERO6) + np.asarray(
                res.reactions[tg])
        for uid, f in res.member_forces.items():
            acc["mf"][uid] = acc["mf"].get(uid, np.zeros(12)) + np.asarray(f)
        for uid, st in res.member_stations.items():
            e = acc["st"].setdefault(uid, {"x": list(st["x"])})
            for k3 in ("N", "V2", "V3", "T", "M2", "M3"):
                e[k3] = e.get(k3, np.zeros(len(st[k3]))) + np.asarray(st[k3])
        for s_name, vals in res.story.items():
            sa = acc["story"].setdefault(s_name, {k4: 0.0 for k4 in vals})
            for k4, v in vals.items():
                sa[k4] += v
        # per-object global nodal forces (for later removal)
        mlook = {m.uid: m for m in model.members}
        for uid, f in res.member_forces.items():
            m = mlook.get(uid)
            if m is None:
                continue
            xax, yax, zax, _, _ = _local_axes(m)
            f = np.asarray(f)
            ent = acc["ele"].setdefault(("members", uid), {})
            for p, o in ((m.pi, 0), (m.pj, 6)):
                F = f[o] * np.asarray(xax) + f[o + 1] * np.asarray(yax) \
                    + f[o + 2] * np.asarray(zax)
                M = f[o + 3] * np.asarray(xax) + f[o + 4] * np.asarray(yax) \
                    + f[o + 5] * np.asarray(zax)
                k = _pkey(tuple(p))
                ent[k] = ent.get(k, _ZERO6) + np.concatenate([F, M])

        def ele_nodal(kind, uid, etag):
            nodes = ops.eleNodes(etag)
            fv = ops.eleForce(etag)
            ndf = len(fv) // len(nodes)
            ent = acc["ele"].setdefault((kind, uid), {})
            for i, nt in enumerate(nodes):
                c = asm.node_coords.get(nt)
                if c is None:
                    continue
                v = np.zeros(6)
                v[:min(ndf, 6)] = fv[i * ndf:i * ndf + min(ndf, 6)]
                k = _pkey(c)
                ent[k] = ent.get(k, _ZERO6) + v

        for uid, etag in asm.link_ele.items():
            ele_nodal("links", uid, etag)
        for q, etag in zip(asm.shell_quads, asm.quad_ele):
            ele_nodal("shells", q["region"], etag)
        return res, asm

    def full_state(label: str) -> CaseResults:
        node_disp = {tg: [float(v) for v in acc["disp"].get(_pkey(c),
                                                           _ZERO6)]
                     for tg, c in full_asm.struct_coords.items()}
        for s_name, mt in full_asm.masters.items():
            node_disp[mt] = [float(v) for v in
                             acc["master"].get(s_name, _ZERO6)]
        reactions = {tg: [float(v) for v in
                          acc["reac"].get(_pkey(full_asm.node_coords[tg]),
                                          _ZERO6)]
                     for tg in full_asm.support_tags}
        base = {k: 0.0 for k in _FORCE_KEYS}
        for k, r in acc["reac"].items():
            x, y, z = k
            fx, fy, fz, mx, my, mz = (float(v) for v in r)
            base["FX"] += fx
            base["FY"] += fy
            base["FZ"] += fz
            base["MX"] += mx + y * fz - z * fy
            base["MY"] += my + z * fx - x * fz
            base["MZ"] += mz + x * fy - y * fx
        member_forces, stations = {}, {}
        for m in model.members:
            member_forces[m.uid] = [float(v) for v in
                                    acc["mf"].get(m.uid, np.zeros(12))]
            st = acc["st"].get(m.uid)
            if st is None:
                ref = oneshot.member_stations.get(m.uid)
                if ref is None:
                    continue
                st = {"x": list(ref["x"]),
                      **{k: np.zeros(len(ref["x"]))
                         for k in ("N", "V2", "V3", "T", "M2", "M3")}}
            stations[m.uid] = {k: (list(v) if k == "x"
                                   else [float(x) for x in v])
                               for k, v in st.items()}
        empty = {"ux": 0.0, "uy": 0.0, "drift_x": 0.0, "drift_y": 0.0,
                 "shear_x": 0.0, "shear_y": 0.0}
        story = {s.name: dict(acc["story"].get(s.name, empty))
                 for s in model.stories}
        return CaseResults(label, node_disp, reactions, base, member_forces,
                           story, stations)

    def td_scales(T_s: float) -> Dict[Tuple[str, str], float]:
        out = {}
        if tdn is None:
            return out
        dur = (math.inf if tdn["t_eval"] == math.inf
               else max(tdn["t_eval"] - T_s, 0.0))
        cr = 1.0 + TD_CHI * aci209_creep(tdn["phi_inf"], dur)
        for kind in ("members", "shells"):
            for uid in active[kind]:
                age = max(T_s - t_cast[(kind, uid)], 0.0)
                e_fac = aci209_modulus_growth(age) if tdn["aging"] else 1.0
                out[(kind, uid)] = e_fac / cr
        return out

    stage_out: List[dict] = []
    for si, st in enumerate(stages):
        T_s = t_start[si]
        release: Dict[tuple, np.ndarray] = {}
        loads = []
        for op in st["operations"]:
            g = groups[op["group"]]
            if op["op"] == "add":
                for kind in GROUP_KINDS:
                    for uid in g[kind]:
                        active[kind].add(uid)
                        t_cast[(kind, uid)] = T_s - float(op["age_days"])
                active_pts.update(_pkey(tuple(p)) for p in g["points"])
            elif op["op"] == "remove":
                for kind in GROUP_KINDS:
                    for uid in g[kind]:
                        active[kind].discard(uid)
                        for k, f in acc["ele"].pop((kind, uid), {}).items():
                            release[k] = release.get(k, _ZERO6) + f
                        if kind == "members":
                            acc["mf"].pop(uid, None)
                            acc["st"].pop(uid, None)
                active_pts.difference_update(_pkey(tuple(p))
                                             for p in g["points"])
            else:
                loads.append(op)
        keys = _active_keys(model, active, lookup)
        pat = LoadPattern("__ustage_pat__", "other")
        for op in loads:
            g = groups[op["group"]]
            objs = {k: set(g[k]) & active[k] for k in GROUP_KINDS}
            _expand_load(model, model.patterns[op["pattern"]],
                         float(op["scale"]), g, objs, lookup, pat, warn)
        label = f"stage {st['name']}"
        dropped = [nl for nl in pat.nodal_loads
                   if _pkey(tuple(nl.point)) not in keys]
        if dropped:
            warn.append(f"{label}: {len(dropped)} joint load(s) at points "
                        "with no active object were skipped")
            pat.nodal_loads = [nl for nl in pat.nodal_loads
                               if _pkey(tuple(nl.point)) in keys]
        if keys:
            sups, springs, lsp = supports_for(keys, label)
            # support keys of this stage: drop reactions of lost supports
            for k, f in release.items():
                if k in keys and np.any(f):
                    pat.nodal_loads.append(NodalLoad(k, *[float(v)
                                                          for v in f]))
            if not _pattern_empty(pat) or release:
                sub = _submodel(model, active, keys, sups, springs, lsp,
                                label)
                if tdn is not None:
                    _apply_td_scales(sub, td_scales(T_s), tdn["materials"])
                _, asm = solve(sub, pat)
                live = {_pkey(asm.node_coords[tg]) for tg in asm.support_tags}
                acc["reac"] = {k: v for k, v in acc["reac"].items()
                               if k in live}
        elif not _pattern_empty(pat):
            raise ValueError(f"Staged case {name!r}: {label}: loads applied "
                             "with no active objects")
        else:
            acc["reac"] = {}
        snap = full_state(f"{name} @ {st['name']}")
        stage_out.append({
            "name": st["name"], "duration_days": st["duration_days"],
            "t_start": T_s, "t_end": T_s + st["duration_days"],
            "active": {k: sorted(active[k]) for k in GROUP_KINDS},
            "node_disp": {str(tg): v for tg, v in snap.node_disp.items()},
            "reactions": {str(tg): v for tg, v in snap.reactions.items()},
            "base": dict(snap.base),
            "member_forces": {u: v for u, v in snap.member_forces.items()},
        })

    final_keys = _active_keys(model, active, lookup)
    if final_keys and tdn is not None and tdn["eps_inf"] > 0.0:
        # final shrinkage increment on the final active structure
        sups, springs, lsp = supports_for(final_keys, "shrinkage")
        sub = _submodel(model, active, final_keys, sups, springs, lsp,
                        "shrinkage")
        scales, pat = {}, LoadPattern("__ustd_shrink__", "other")
        model_alpha = getattr(model, "thermal_alpha", 1.2e-5)
        for kind in ("members", "shells"):
            for uid in active[kind]:
                age = (math.inf if tdn["t_eval"] == math.inf else
                       max(tdn["t_eval"] - t_cast[(kind, uid)], 0.0))
                scales[(kind, uid)] = 1.0 / (
                    1.0 + TD_CHI * aci209_creep(tdn["phi_inf"], age))
                if kind != "members":
                    continue
                m = lookup["members"][uid]
                sec = model.sections.get(m.section)
                if sec is None or sec.material not in tdn["materials"]:
                    continue
                eps = aci209_shrinkage(tdn["eps_inf"], age)
                if eps > 0.0:
                    mat = model.materials.get(sec.material)
                    a = getattr(mat, "alpha", None) if mat else None
                    alpha = a if a is not None else model_alpha
                    pat.thermal_loads.append(ThermalLoad(uid, -eps / alpha))
        _apply_td_scales(sub, scales, tdn["materials"])
        if not _pattern_empty(pat):
            solve(sub, pat)
    if final_keys and sc.include_live:
        sups, springs, lsp = supports_for(final_keys, "include_live")
        sub = _submodel(model, active, final_keys, sups, springs, lsp,
                        "include_live")
        live = LoadPattern("__ustage_none__", "other")
        solve(sub, live, extra=dict(sc.include_live))

    case = full_state(name)
    if warn:
        case.warning = "; ".join(sorted(set(warn)))
        for w in sorted(set(warn)):
            warnings.warn(f"Staged case {name!r}: {w}", UserWarning)
    cols = [m.uid for m in model.members if m.kind == "column"]
    pct = 0.0
    if cols:
        ref = max(abs(oneshot.member_forces[u][0]) for u in cols)
        if ref > 0.0:
            pct = 100.0 * max(abs(case.member_forces[u][0]
                                  - oneshot.member_forces[u][0])
                              for u in cols) / ref
    return StagedResults(name, case, oneshot, pct, shortening=None,
                         stages=stage_out)
